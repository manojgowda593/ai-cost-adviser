"""
agent.py — tool-calling cost-analysis agent (step ⑤, agentic version).

Rather than the code pre-deciding what data to feed the model, the AGENT decides:
it sees the scanned resources, then calls read-only AWS tools (CPU metrics,
Compute Optimizer, Cost Explorer, deeper describe) as IT sees fit, reasons over
the results, and returns the final cost analysis JSON.

Flow (OpenAI-style function calling):
  1. system + user(resources) ->
  2. model responds with either tool_calls or a final answer
  3. if tool_calls: execute each via agent_tools.TOOL_IMPLS, append results,
     loop back to (2)
  4. if final answer: parse JSON and return

Safety: MAX_ITERATIONS caps the loop. If the model/provider doesn't support
tool calling reliably, callers can fall back to ai_analyzer.analyze_resources.
"""

from __future__ import annotations

import json
import os

from openai import OpenAI
from openai import APIError, APIConnectionError, AuthenticationError, RateLimitError

from agent_tools import TOOL_IMPLS, TOOL_SPECS
from ai_analyzer import AIAnalyzerError  # reuse the same error type


MAX_ITERATIONS = 8  # hard cap on tool-call rounds per analysis


AGENT_SYSTEM_PROMPT = """You are an autonomous AWS cost-optimization agent. Your ONLY
goal is to find ways to REDUCE the monthly AWS bill — and to advise like a senior
cloud cost engineer would: specific, evidence-backed, and SAFE.

You have ONE universal tool — `aws_api` — that calls any read-only AWS API on any
service via boto3. You decide what to investigate; there are no fixed steps. Use it
freely to gather whatever evidence each resource needs, for example:
  - Over-provisioned / idle compute: cloudwatch get_metric_statistics
    (Namespace AWS/EC2, MetricName CPUUtilization) for avg/max CPU over time.
  - AWS's own right-sizing: compute-optimizer get_ec2_instance_recommendations.
  - Real spend to ground estimates: ce get_cost_and_usage.
  - Deeper config: ec2 describe_volumes / describe_snapshots, rds
    describe_db_instances, s3 get_bucket_lifecycle_configuration, dynamodb
    describe_table — or ANY other read API for any service.
  - Other metrics: cloudwatch get_metric_statistics works for RDS connections,
    ELB request counts, DynamoDB consumed capacity, etc. — pick the right
    Namespace/MetricName/Dimensions for the resource.

aws_api is READ-ONLY (mutating calls are blocked). If a call returns
{"available": false}, that signal/permission is unavailable — note it, try a
different read, or reason from what you have; never fabricate numbers. Do not give
up on a resource because of a failed call — investigate with what works.

HOW TO REASON (apply to EVERY resource type, not just the examples):
• Investigate first. Pull metrics/history for anything that might be over-sized or
  idle before judging it. Reason from the resource's OWN data and history.
• Name the resource concretely: its id AND name, and the specific numbers/history
  that justify your call.
• Explain WHY the change is safe and still meets the workload, not just "it's cheaper".
• ALWAYS include a relevant SAFETY caveat in "caveats" — the kind of risk depends on
  the action:
    - Destructive (delete/detach volume, snapshot, DB, bucket): warn the data could
      be lost; tell the user to verify nothing critical is there and snapshot first.
      Set requires_data_check = true.
    - Downsize / right-size compute: warn that average usage hides PEAKS. Tell the
      user to check for periodic spikes (batch jobs, traffic peaks, month-end) before
      downsizing, and to consider auto-scaling so sudden spikes are still handled.
    - Other changes (retention, storage class, etc.): note any tradeoff (e.g. shorter
      log retention means older logs are gone).
  Every issue must have a caveats string — never leave it empty.

GOOD recommendations read like these — DETAILED, like a senior engineer (match this
depth, generically for any resource):
• current_state: "Volume vol-0abc (data-old) is in 'available' state — unattached for
  ~30 days, still billing for 100 GB gp3 (~$8/mo)."
  recommendation: "It serves no instance. Delete it to stop the charge."
  caveats: "Deleting is irreversible. Confirm the volume holds no critical data; take
  a snapshot first if you're unsure, then delete."
• current_state: "Instance i-0xyz (api-server) is m5.xlarge, but max CPU was only 1%
  and average ~0.5% over the last 7 days — heavily oversized for this load."
  recommendation: "Downsize to m5.large, which comfortably handles this workload at
  roughly half the compute cost."
  caveats: "Average usage can hide spikes — check for periodic peaks (batch jobs,
  traffic surges) before resizing. If sudden spikes are possible, configure an
  Auto Scaling group so capacity grows automatically instead of running a large
  instance 24/7."
• current_state: "Log group /aws/lambda/foo has no retention policy — logs accumulate
  forever, so storage cost grows unbounded."
  recommendation: "Set a 30-day retention to cap storage cost."
  caveats: "Logs older than 30 days will be deleted — confirm you don't need them for
  audit/compliance before applying."

STRICT RULES:
1. Only report an issue if acting on it LOWERS cost. Skip already-efficient resources.
2. NEVER suggest an action that increases or maintains cost (no starting/enlarging).
3. Every issue MUST be backed by EVIDENCE you gathered (tool result or scan config),
   cited with concrete numbers/history. Don't claim over-provisioning without metrics.
4. If a tool returns {"available": false}, that signal is unavailable — say so and
   reason from what you have (e.g. "couldn't read CPU metrics; based on config…"),
   never fabricate numbers.
5. Do not invent resources, ids, metrics, or numbers.
6. For any destructive fix, set "requires_data_check": true and spell out the
   safety step in "caveats".

Investigate as needed using tools. When done, respond with ONLY a JSON object
(no markdown) matching this schema:
{
  "summary": string,                       // 1-3 sentences: overall picture + total opportunity
  "total_estimated_savings_usd": number,
  "issues": [
    {
      "service": string,
      "category": string,
      "resource_id": string,               // the real id, e.g. i-0abc / vol-0abc
      "resource_name": string,             // the Name tag or id if untagged
      "issue": string,                     // short title, e.g. "Over-provisioned instance"
      "severity": "high"|"medium"|"low",   // by savings size + confidence
      "current_state": string,             // the EVIDENCE: type/size + metrics + history
                                           // e.g. "m5.xlarge, max CPU 2% / mem 10% over 14d"
      "recommendation": string,            // the reasoned action + why it's safe & sufficient
                                           // e.g. "Downsize to m5.large — handles this load, ~50% cheaper"
      "estimated_savings_usd": number,     // best-effort MONTHLY usd, 0 only if truly unknown
      "requires_data_check": boolean,      // true for delete/detach/destructive actions
      "caveats": string,                   // safety notes; "" if none. For destructive:
                                           // "Verify no critical data; snapshot before deleting."
      "fix_command": string                // valid cost-REDUCING AWS CLI command, or "" if none
    }
  ]
}
If nothing can reduce cost, return "issues": [] with a summary saying resources look
efficient. An empty list is the correct answer when everything is right-sized."""


def _client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise AIAnalyzerError(
            "OPENAI_API_KEY is not set.",
            hint="Set OPENAI_API_KEY in the root .env (OpenAI or Groq key).",
        )
    return OpenAI(api_key=api_key, base_url=os.getenv("OPENAI_BASE_URL"))


def _execute_tool_call(call) -> str:
    """Run one tool call and return its JSON-string result for the model."""
    name = call.function.name
    try:
        args = json.loads(call.function.arguments or "{}")
    except json.JSONDecodeError:
        args = {}
    impl = TOOL_IMPLS.get(name)
    if impl is None:
        return json.dumps({"available": False, "reason": f"Unknown tool '{name}'."})
    try:
        result = impl(**args)
    except TypeError as e:
        # bad/missing args from the model — report back instead of crashing
        result = {"available": False, "reason": f"Invalid arguments for {name}: {e}"}
    return json.dumps(result, default=str)


def analyze_resources_agentic(resources: list[dict], region: str | None = None) -> dict:
    """
    Agentic analysis. Returns the same shape as ai_analyzer.analyze_resources,
    plus a "_agent" key describing how many tool calls were made (for transparency).
    Raises AIAnalyzerError on auth/connection/parse failure so the caller can
    fall back to the non-agentic analyzer.
    """
    if not resources:
        return {
            "summary": "No resources were found to analyze.",
            "total_estimated_savings_usd": 0,
            "issues": [],
        }

    client = _client()
    model = os.getenv("OPENAI_MODEL", "gpt-4o")

    messages = [
        {"role": "system", "content": AGENT_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"AWS region: {region or 'default'}\n"
                f"Here are the scanned resources. Investigate with tools as needed, then "
                f"return the cost analysis JSON.\n\n{json.dumps(resources, indent=2, default=str)}"
            ),
        },
    ]

    tool_calls_made = 0
    try:
        for _ in range(MAX_ITERATIONS):
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=TOOL_SPECS,
                tool_choice="auto",
                temperature=0.2,
            )
            msg = resp.choices[0].message

            if msg.tool_calls:
                # Record the assistant's tool-call turn, then answer each call.
                messages.append(
                    {
                        "role": "assistant",
                        "content": msg.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                            }
                            for tc in msg.tool_calls
                        ],
                    }
                )
                for tc in msg.tool_calls:
                    tool_calls_made += 1
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": _execute_tool_call(tc),
                        }
                    )
                continue  # loop back so the model sees the tool results

            # No tool calls -> this is the final answer.
            analysis = _parse_final(msg.content or "")
            analysis["_agent"] = {"tool_calls": tool_calls_made, "model": model}
            return analysis

        # Hit the iteration cap without a final answer — ask once for the JSON.
        messages.append(
            {"role": "user", "content": "Stop investigating. Return the final cost analysis JSON now."}
        )
        resp = client.chat.completions.create(
            model=model, messages=messages, temperature=0.2,
            response_format={"type": "json_object"},
        )
        analysis = _parse_final(resp.choices[0].message.content or "")
        analysis["_agent"] = {"tool_calls": tool_calls_made, "model": model, "hit_cap": True}
        return analysis

    except AuthenticationError:
        raise AIAnalyzerError("The AI provider rejected the API key.",
                              hint="Check OPENAI_API_KEY / OPENAI_BASE_URL in .env.")
    except RateLimitError:
        raise AIAnalyzerError("AI provider rate limit or quota exceeded.")
    except APIConnectionError:
        raise AIAnalyzerError("Could not connect to the AI provider.")
    except APIError as e:
        raise AIAnalyzerError(f"AI provider API error: {e}")


def _parse_final(content: str) -> dict:
    """Parse the model's final JSON, tolerating accidental markdown fencing."""
    text = content.strip()
    if text.startswith("```"):
        # strip ```json ... ``` fences if the model added them
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        analysis = json.loads(text)
    except json.JSONDecodeError:
        raise AIAnalyzerError(
            "The AI returned a response that was not valid JSON.",
            hint="The model may not support tool-calling reliably; consider the non-agentic analyzer.",
        )
    analysis.setdefault("summary", "")
    analysis.setdefault("total_estimated_savings_usd", 0)
    analysis.setdefault("issues", [])
    return analysis
