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
SYSTEM_PROMPT = """You are an expert AWS cloud cost optimization analyst.
You are given a list of AWS resources with their configuration. Analyze them for:
  - over-provisioning (resources larger/more powerful than needed)
  - unused or idle resources (e.g. unattached EBS volumes, stopped-but-billed resources)
  - misconfigurations (e.g. gp2 volumes that should be gp3, public DBs)
  - wrong pricing tiers / instance families (e.g. old-generation instance types)
  - general cost optimization opportunities (reserved capacity, lifecycle policies)

Be specific and conservative: only flag an issue when the provided configuration
supports it. Do not invent resources or fields that are not present.

Every fix_command MUST be a valid AWS CLI command the user can run to remediate
the issue (e.g. `aws ec2 ...`, `aws s3api ...`, `aws rds ...`). If no safe CLI
fix exists, set fix_command to an empty string.

Respond with ONLY a JSON object (no markdown, no prose) matching this schema:
{
  "summary": string,
  "total_estimated_savings_usd": number,
  "issues": [
    {
      "service": string,
      "category": string,
      "resource_id": string,
      "issue": string,
      "severity": "high" | "medium" | "low",
      "estimated_savings_usd": number,
      "fix_command": string,
      "rationale": string
    }
  ]
}
If there are no issues, return an empty issues array and a summary saying so."""


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
