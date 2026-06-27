"""
ai_analyzer.py — Step ⑤ of the request flow (AWS version).

Takes the list of AWS resources produced by aws_scanner.py and asks the OpenAI
API (gpt-4o) to analyze them for cost problems, returning a structured result:

    {
        "summary":            "<plain-language overview>",
        "total_estimated_savings_usd": <number>,
        "issues": [
            {
                "service":        "EC2",
                "category":       "Compute",
                "resource_id":    "i-0abc123",
                "issue":          "Instance appears over-provisioned",
                "severity":       "high" | "medium" | "low",
                "estimated_savings_usd": <number>,
                "fix_command":    "aws ec2 modify-instance-attribute ...",
                "rationale":      "<why>"
            },
            ...
        ]
    }

This mirrors the Azure reference 1:1, with two AWS adaptations:
  • input resources come from boto3 (AWS), not the Azure CLI
  • fix_command values are AWS CLI commands, not Azure CLI commands

NOTE: the deterministic rule engine + AWS Compute Optimizer / Cost Explorer
inputs are a LATER prompt. This module is LLM-only, as specified.
"""

from __future__ import annotations

import json
import os

from openai import OpenAI
from openai import (
    APIError,
    APIConnectionError,
    AuthenticationError,
    RateLimitError,
)


# The OpenAI SDK is wire-compatible with several providers (OpenAI, Groq,
# OpenRouter, Gemini's compat endpoint, …). Point it at a different provider by
# setting OPENAI_BASE_URL + a matching OPENAI_MODEL; no code change needed.
#   OpenAI: base_url unset, model "gpt-4o"
#   Groq:   OPENAI_BASE_URL=https://api.groq.com/openai/v1, model e.g.
#           "llama-3.3-70b-versatile"
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
BASE_URL = os.getenv("OPENAI_BASE_URL")  # None -> default OpenAI endpoint


class AIAnalyzerError(Exception):
    """Raised when the AI analysis cannot be produced, with a user-facing hint."""

    def __init__(self, message: str, *, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint


# --------------------------------------------------------------------------- #
# Prompt construction                                                          #
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = """You are an expert AWS cost-optimization analyst. Your ONLY job
is to find opportunities to REDUCE cost in the given AWS resources.

STRICT RULES — follow exactly:
1. Only report an issue if acting on it would LOWER the monthly bill. If a
   resource is already cost-efficient, do NOT report it.
2. NEVER suggest an action that increases or maintains cost (e.g. starting,
   enlarging, or launching a resource). Those are not cost optimizations.
3. Base every issue on EVIDENCE actually present in the data — the resource's
   config and, where present, its `cpu_utilization` metrics (avg/max CPU % over
   a lookback window). If there is no evidence of waste, do not invent one.
4. Cite the evidence in `rationale` (e.g. "avg CPU 2.1% over 14 days, max 9%").
   If you claim over-provisioning, the cpu_utilization metrics MUST support it
   (low average CPU). With no metrics, do NOT claim over-provisioning — only
   config-based facts (e.g. an unattached/`available` EBS volume, a gp2 volume
   that could be gp3, no S3 lifecycle policy).
5. Do not invent resources, fields, IDs, or metrics not in the input.
6. SAFETY: every issue MUST have a relevant caveat (never empty). The risk depends on
   the action:
     - Destructive (delete/detach volume/snapshot/DB/bucket): set
       "requires_data_check": true; tell the user to verify there is no critical data
       and snapshot first before deleting.
     - Downsize/right-size compute: warn that average usage hides PEAKS — check for
       periodic spikes before resizing, and consider auto-scaling so sudden spikes are
       still handled.
     - Other (retention, storage class…): note the tradeoff (e.g. older logs deleted).

Advise like a senior cloud engineer — DETAILED and reasoned. For each issue, fill
current_state with the EVIDENCE (size + metrics + history), recommendation with the
reasoned action + why it still meets the workload, and caveats with the safety note.
Depth expected (apply generically to any resource):
  - current_state: "Volume vol-0abc (data-old) is 'available' (unattached), still
     billing for its size."
    recommendation: "Delete it to stop the charge."
    caveats: "Irreversible — verify no critical data; snapshot first if unsure."
  - current_state: "Instance i-0xyz (api) is m5.xlarge but max CPU was 1% over 7 days
     — oversized for this load."
    recommendation: "Downsize to m5.large, which still handles it, to ~halve cost."
    caveats: "Averages hide spikes — check for periodic peaks before resizing; add
     auto-scaling if sudden spikes are possible."

`estimated_savings_usd` is your best-effort MONTHLY USD estimate; use 0 only if you
genuinely cannot estimate. `fix_command` MUST be a valid, cost-REDUCING AWS CLI
command (e.g. `aws ec2 modify-instance-attribute ...`, `aws ec2 delete-volume ...`),
or "" if none applies.

Respond with ONLY a JSON object (no markdown, no prose) matching this schema:
{
  "summary": string,
  "total_estimated_savings_usd": number,
  "issues": [
    {
      "service": string,
      "category": string,
      "resource_id": string,
      "resource_name": string,
      "issue": string,
      "severity": "high" | "medium" | "low",
      "current_state": string,
      "recommendation": string,
      "estimated_savings_usd": number,
      "requires_data_check": boolean,
      "caveats": string,
      "fix_command": string
    }
  ]
}
If nothing can reduce cost, return "issues": [] and say so in the summary. An
empty list is the correct, expected answer when resources are already efficient."""


def _build_user_prompt(resources: list[dict], region: str | None) -> str:
    return (
        f"AWS region: {region or 'default'}\n"
        f"Resource count: {len(resources)}\n\n"
        "Here are the resources to analyze (JSON):\n"
        f"{json.dumps(resources, indent=2, default=str)}"
    )


# --------------------------------------------------------------------------- #
# OpenAI call                                                                  #
# --------------------------------------------------------------------------- #
def _get_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise AIAnalyzerError(
            "OPENAI_API_KEY is not set.",
            hint="Set OPENAI_API_KEY in the root .env (your OpenAI or Groq key). "
            "For Groq, also set OPENAI_BASE_URL=https://api.groq.com/openai/v1.",
        )
    # Read base_url at call time (not import time). None -> default OpenAI endpoint.
    return OpenAI(api_key=api_key, base_url=os.getenv("OPENAI_BASE_URL"))


def analyze_resources(resources: list[dict], region: str | None = None) -> dict:
    """
    Send the scanned AWS resources to OpenAI and return the structured analysis.

    If there are no resources, we skip the API call entirely (saves cost and
    avoids a meaningless request).
    """
    if not resources:
        return {
            "summary": "No resources were found to analyze.",
            "total_estimated_savings_usd": 0,
            "issues": [],
        }

    client = _get_client()

    # Read the model at call time (not import time) so it always reflects the
    # current environment, regardless of when .env was loaded.
    model = os.getenv("OPENAI_MODEL", "gpt-4o")

    try:
        response = client.chat.completions.create(
            model=model,
            # response_format forces strict JSON so we can parse reliably.
            response_format={"type": "json_object"},
            temperature=0.2,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(resources, region)},
            ],
        )
    except AuthenticationError:
        raise AIAnalyzerError(
            "OpenAI rejected the API key.",
            hint="Check that OPENAI_API_KEY in backend/.env is valid and has credit.",
        )
    except RateLimitError:
        raise AIAnalyzerError(
            "OpenAI rate limit or quota exceeded.",
            hint="Wait and retry, or check your OpenAI account billing/quota.",
        )
    except APIConnectionError:
        raise AIAnalyzerError(
            "Could not connect to the OpenAI API.",
            hint="Check your network connection.",
        )
    except APIError as e:
        raise AIAnalyzerError(f"OpenAI API error: {e}")

    content = response.choices[0].message.content or ""
    try:
        analysis = json.loads(content)
    except json.JSONDecodeError:
        raise AIAnalyzerError(
            "The AI returned a response that was not valid JSON.",
            hint="Retry the analysis; if it persists, the model output may need adjusting.",
        )

    # Defensive normalization so the API/UI always sees a consistent shape.
    analysis.setdefault("summary", "")
    analysis.setdefault("total_estimated_savings_usd", 0)
    analysis.setdefault("issues", [])
    return analysis
