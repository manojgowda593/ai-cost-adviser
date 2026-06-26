"""
main.py — FastAPI backend for the AI Cloud Cost Detective (AWS version).

This covers step ③ of the request flow: fetching resource config for the
services the user selected. Detection rules, AWS recommendation APIs, the
LLM summary, and persistence come in later prompts.

Endpoints
---------
GET  /api/services   -> list of scannable AWS services (replaces Azure's
                        GET /api/resource-groups, since AWS has no resource groups
                        and you chose per-service selection).
POST /api/analyze    -> { "services": ["ec2","s3"], "region": "ap-south-1" }
                        runs the selected scanners and returns structured resources.
"""

from __future__ import annotations

from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import aws_scanner
from aws_scanner import (
    ScannerError,
    NoCredentialsConfigured,
    AccessDenied,
    InvalidRegion,
)
import ai_analyzer
from ai_analyzer import AIAnalyzerError

# Load backend/.env (OPENAI_API_KEY, OPENAI_MODEL) before anything reads os.getenv.
load_dotenv()

app = FastAPI(title="AI Cloud Cost Detective — API", version="0.1.0")

# CORS for the Vite dev server (same origin the Azure prompt specified).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Request / response models                                                    #
# --------------------------------------------------------------------------- #
class AnalyzeRequest(BaseModel):
    # AWS analogue of { "resource_group": "<name>" }: pick services + a region.
    services: list[str] = Field(
        ...,
        description="Service keys to scan, e.g. ['ec2', 'ebs', 's3', 'rds'].",
        examples=[["ec2", "ebs"]],
    )
    region: Optional[str] = Field(
        default=None,
        description="AWS region for regional services, e.g. 'ap-south-1'. "
        "Falls back to the environment/profile default if omitted.",
    )


# --------------------------------------------------------------------------- #
# Error mapping — turn scanner errors into clean HTTP responses                #
# --------------------------------------------------------------------------- #
def _raise_http(e: ScannerError):
    # 401 = credentials problem, 403 = permission, 400 = bad input/region, 500 = other.
    if isinstance(e, NoCredentialsConfigured):
        status = 401
    elif isinstance(e, AccessDenied):
        status = 403
    elif isinstance(e, InvalidRegion):
        status = 400
    else:
        status = 500
    raise HTTPException(status_code=status, detail={"message": e.message, "hint": e.hint})


# --------------------------------------------------------------------------- #
# Endpoints                                                                    #
# --------------------------------------------------------------------------- #
@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/services")
def get_services():
    """List the AWS services this tool can scan (replaces /api/resource-groups)."""
    return {"services": aws_scanner.list_services()}


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest):
    """
    Full pipeline for this prompt:
      Step ③ — scan the selected services (aws_scanner) into the uniform shape.
      Step ⑤ — pass those resources to the AI analyzer (OpenAI gpt-4o) and
                attach the structured cost analysis.

    Individual service scan failures are reported in `errors` without failing
    the whole request; only setup-level problems (no creds, no services,
    unknown service) or an AI failure raise an HTTP error.
    """
    # --- Step ③: scan ---
    try:
        scan = aws_scanner.scan_services(req.services, req.region)
    except (NoCredentialsConfigured, AccessDenied, InvalidRegion) as e:
        _raise_http(e)
    except ScannerError as e:
        # Bad/empty/unknown service selection -> 400.
        raise HTTPException(status_code=400, detail={"message": e.message, "hint": e.hint})

    # --- Step ⑤: AI analysis ---
    try:
        analysis = ai_analyzer.analyze_resources(scan["resources"], scan["region"])
    except AIAnalyzerError as e:
        # Missing key / bad key / rate limit / bad output. 502 = upstream AI failure,
        # 400 = our own misconfiguration (no key set).
        status = 400 if "OPENAI_API_KEY is not set" in e.message else 502
        raise HTTPException(status_code=status, detail={"message": e.message, "hint": e.hint})

    # Return the scan (raw resources + per-service errors) alongside the analysis,
    # so the UI can show both what was found and what to do about it.
    return {**scan, "analysis": analysis}
