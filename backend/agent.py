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
goal is to find ways to REDUCE the monthly AWS bill for the given resources.

You have tools to investigate each resource. Decide for yourself which tools to call:
  - get_cpu_metrics: is a running instance over-provisioned (low avg CPU) or idle?
  - get_compute_optimizer_recommendation: AWS's own right-sizing advice (best signal).
  - get_service_cost: the ACTUAL dollars a service costs (ground your estimates).
  - describe_resource: deeper config when scan data is insufficient.

STRICT RULES:
1. Only report an issue if acting on it LOWERS cost. Skip already-efficient resources.
2. NEVER suggest an action that increases or maintains cost (no starting/enlarging/
   launching). Those are not optimizations.
3. Every issue MUST be backed by EVIDENCE you gathered (a tool result or scan config).
   Cite it in `rationale` (e.g. "avg CPU 2.1% over 14 days" or "Compute Optimizer
   finding OVER_PROVISIONED"). Do not claim over-provisioning without supporting data.
4. If a tool returns {"available": false}, that signal is unavailable — do not
   fabricate it; reason from what you do have.
5. Do not invent resources, ids, metrics, or numbers.

Investigate as needed using tools. When done, respond with ONLY a JSON object
(no markdown) matching this schema:
{
  "summary": string,
  "total_estimated_savings_usd": number,
  "issues": [
    {"service": string, "category": string, "resource_id": string, "issue": string,
     "severity": "high"|"medium"|"low", "estimated_savings_usd": number,
     "fix_command": string, "rationale": string}
  ]
}
If nothing can reduce cost, return "issues": [] — an empty list is the correct
answer when resources are already efficient. fix_command must be a valid,
cost-REDUCING AWS CLI command, or "" if none applies."""


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
