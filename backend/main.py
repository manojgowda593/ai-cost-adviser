"""
main.py — FastAPI backend for the AI Cloud Cost Detective (AWS version).

Covers steps ③ ④ ⑤ ⑥ of the request flow:
  ③ scan selected AWS services (aws_scanner)
  ④ push live progress over WebSocket (progress hub)
  ⑤ AI cost analysis (ai_analyzer, OpenAI gpt-4o)
  ⑥ persist the result to PostgreSQL (db)

Endpoints
---------
GET  /api/services                  -> scannable AWS services (replaces Azure's
                                       /api/resource-groups).
POST /api/analyze                   -> start an analysis; returns its analysis_id
                                       immediately, then runs scan+AI+store in the
                                       background while pushing progress over WS.
GET  /api/history                   -> past analyses for the (eventual) user.
WS   /ws/progress/{analysis_id}     -> live progress for one analysis.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
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
import db
from db import DBNotConfigured
from progress import hub

# Load backend/.env (OPENAI_API_KEY, OPENAI_MODEL, DATABASE_URL) before os.getenv reads.
load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: connect DB + create tables. If DATABASE_URL is unset, run without
    # persistence so non-DB endpoints still work in local dev.
    app.state.db_connected = await db.init_db()
    yield
    # Shutdown: close the pool cleanly.
    await db.close_db()


app = FastAPI(title="AI Cloud Cost Detective — API", version="0.1.0", lifespan=lifespan)

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
    # TODO: replace with the authenticated user's id once JWT auth is added
    # (later prompt). For now the client may pass a user_id, or omit it.
    user_id: Optional[int] = Field(default=None, description="Owning user id (temp).")


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


def _validate_services(services: list[str]) -> None:
    """Cheap up-front validation so we fail fast (400) before creating a row."""
    if not services:
        raise HTTPException(status_code=400, detail={"message": "No services selected."})
    unknown = [s for s in services if s not in aws_scanner.SERVICE_REGISTRY]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail={"message": f"Unknown service(s): {', '.join(unknown)}."},
        )


async def _run_analysis(analysis_id: str, req: AnalyzeRequest) -> dict:
    """
    The full ③→④→⑤→⑥ pipeline, pushing progress over the WebSocket at each
    stage. Scanning and the OpenAI call are blocking/sync, so we run them in a
    thread to avoid stalling the event loop (which serves the WebSocket).
    Returns the final result dict.
    """
    services_label = ", ".join(req.services)

    await hub.push(analysis_id, "Preparing scan...")

    # --- Step ③ + ④: scan each service, with a progress message per service ---
    resources: list[dict] = []
    errors: list[dict] = []
    for key in req.services:
        meta = aws_scanner.SERVICE_REGISTRY[key]
        await hub.push(analysis_id, f"Scanning {meta['label']}...")
        try:
            found = await asyncio.to_thread(
                aws_scanner._run_scanner, meta["scan"], meta["label"], req.region
            )
            resources.extend(found)
        except ScannerError as e:
            errors.append({"service": meta["label"], "error": e.message, "hint": e.hint})

    # --- Step ⑤: AI analysis ---
    await hub.push(analysis_id, "Analyzing costs with AI...")
    ai_error: Optional[str] = None
    try:
        analysis = await asyncio.to_thread(
            ai_analyzer.analyze_resources, resources, req.region
        )
    except AIAnalyzerError as e:
        ai_error = e.message
        analysis = {"summary": f"AI analysis failed: {e.message}", "issues": [],
                    "total_estimated_savings_usd": 0}

    # --- Step ⑥: store the result ---
    issues = analysis.get("issues", [])
    savings = str(analysis.get("total_estimated_savings_usd", 0))
    if db.is_connected() and analysis_id.isdigit():
        await hub.push(analysis_id, "Storing results...")
        try:
            if ai_error:
                await db.fail_analysis(int(analysis_id), ai_error)
            else:
                await db.complete_analysis(
                    int(analysis_id),
                    resources_scanned=len(resources),
                    issues_found=len(issues),
                    estimated_savings=savings,
                    analysis_result={"analysis": analysis, "errors": errors},
                )
        except DBNotConfigured:
            pass  # DB went away mid-run; the in-memory result is still returned.

    await hub.push(analysis_id, "Analysis complete")
    hub.clear(analysis_id)

    return {
        "analysis_id": analysis_id,
        "region": req.region,
        "scanned_services": [aws_scanner.SERVICE_REGISTRY[k]["label"] for k in req.services],
        "resource_count": len(resources),
        "resources": resources,
        "errors": errors,
        "analysis": analysis,
        "status": "failed" if ai_error else "complete",
    }


# A counter for in-memory analysis ids when no DB is configured (dev mode).
_mem_counter = {"n": 0}


@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest):
    """
    Start an analysis. Returns immediately with an `analysis_id` plus the final
    result. The client should open ws/progress/{analysis_id} to watch live
    progress; because the pipeline runs while the request is in flight, late
    WebSocket joiners still get the buffered backlog from the progress hub.
    """
    _validate_services(req.services)

    # Create a pending DB row up front so we have a stable analysis_id and a
    # durable record. Falls back to an in-memory id if no DB is configured.
    if db.is_connected():
        try:
            row_id = await db.create_analysis(req.user_id, ", ".join(req.services))
            analysis_id = str(row_id)
        except DBNotConfigured:
            _mem_counter["n"] += 1
            analysis_id = f"mem-{_mem_counter['n']}"
    else:
        _mem_counter["n"] += 1
        analysis_id = f"mem-{_mem_counter['n']}"

    return await _run_analysis(analysis_id, req)


@app.get("/api/history")
async def history(user_id: Optional[int] = None, limit: int = 50):
    """
    Past analyses, newest first. `user_id` is optional for now; once JWT auth
    lands it will come from the authenticated token instead of a query param.
    """
    if not db.is_connected():
        raise HTTPException(
            status_code=503,
            detail={"message": "History unavailable: DATABASE_URL is not configured."},
        )
    return {"history": await db.get_history(user_id, limit=limit)}


@app.websocket("/ws/progress/{analysis_id}")
async def ws_progress(websocket: WebSocket, analysis_id: str):
    """
    Live progress channel for one analysis. The client connects with the
    analysis_id returned by /api/analyze and receives JSON
    {analysis_id, message} for each stage, including any backlog it missed.
    """
    await hub.connect(analysis_id, websocket)
    try:
        # Keep the socket open; we only push server->client. Reading lets us
        # detect disconnects.
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await hub.disconnect(analysis_id, websocket)
