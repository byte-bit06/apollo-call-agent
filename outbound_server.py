"""
outbound_server.py
FastAPI backend for the AI Sales Outbound Dialer System.
Runs on port 8080 (separate from the vapi_webhook.py server on port 8000).

Endpoints:
  GET  /                           Serves the dashboard UI
  POST /api/leads/discover         Starts background lead discovery task
  GET  /api/leads/status/{id}      Live crawl logs & progress
  GET  /api/leads                  List all leads (filterable by status)
  POST /api/leads/add              Manually add a single lead
  PUT  /api/leads/{id}             Edit lead details
  PATCH /api/leads/{id}/status     Manually change lead status
  POST /api/leads/{id}/call        Trigger Vapi outbound call
  POST /webhook                    Vapi webhook receiver (updates call outcomes)
  GET  /api/calls                  Full call log history
  GET  /api/settings               Get current settings
  POST /api/settings               Update settings (.env keys)
"""

import os
import json
import uuid
import hashlib
import secrets
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, Response, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Optional, List
from dotenv import load_dotenv, set_key

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent.resolve()
ENV_PATH = BASE_DIR / ".env"

if not ENV_PATH.exists():
    ENV_PATH.write_text("# AI Sales Outbound Dialer\n")

load_dotenv(str(ENV_PATH))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("outbound_server")

# ---------------------------------------------------------------------------
# Data file helpers
# ---------------------------------------------------------------------------

LEADS_FILE = BASE_DIR / "leads.json"
CALLS_FILE = BASE_DIR / "call_log.json"

_data_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Auth — simple single-user session
# ---------------------------------------------------------------------------

_AUTH_USERNAME = "apollo"
_AUTH_PASSWORD_HASH = hashlib.sha256(b"Apollo@calls21").hexdigest()
_SESSIONS: set = set()   # active session tokens
_SESSION_COOKIE = "apollo_session"


def _check_session(request: Request) -> bool:
    token = request.cookies.get(_SESSION_COOKIE)
    return token is not None and token in _SESSIONS


def _load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default


def _save_json(path: Path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_leads() -> list:
    with _data_lock:
        return _load_json(LEADS_FILE, [])


def save_leads(leads: list):
    with _data_lock:
        _save_json(LEADS_FILE, leads)


def load_calls() -> list:
    with _data_lock:
        return _load_json(CALLS_FILE, [])


def save_calls(calls: list):
    with _data_lock:
        _save_json(CALLS_FILE, calls)


def get_contacted_domains() -> set:
    leads = load_leads()
    return {
        l["domain"]
        for l in leads
        if l.get("status") in ("contacted", "do_not_call")
    }


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------

def get_settings() -> dict:
    return {
        "VAPI_API_KEY": os.getenv("VAPI_API_KEY", ""),
        "VAPI_ASSISTANT_ID": os.getenv("VAPI_ASSISTANT_ID", ""),
        "VAPI_PHONE_NUMBER_ID": os.getenv("VAPI_PHONE_NUMBER_ID", ""),
        "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY", ""),
        "OVERRIDE_SYSTEM_PROMPT": os.getenv("OVERRIDE_SYSTEM_PROMPT", "false"),
        "GOOGLE_CALENDAR_ID": os.getenv("GOOGLE_CALENDAR_ID", "primary"),
        "TIMEZONE": os.getenv("TIMEZONE", "Australia/Melbourne"),
        "GOOGLE_SERVICE_ACCOUNT_JSON": os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", ""),
    }


def update_settings(data: dict):
    allowed = [
        "VAPI_API_KEY", "VAPI_ASSISTANT_ID", "VAPI_PHONE_NUMBER_ID", "GEMINI_API_KEY", "OVERRIDE_SYSTEM_PROMPT",
        "GOOGLE_CALENDAR_ID", "TIMEZONE", "GOOGLE_SERVICE_ACCOUNT_JSON"
    ]
    for key, value in data.items():
        if key in allowed:
            set_key(str(ENV_PATH), key, str(value))
            os.environ[key] = str(value)


# ---------------------------------------------------------------------------
# Background discovery job tracker
# ---------------------------------------------------------------------------

_discovery_jobs: dict = {}  # job_id -> {status, logs, leads_found, cancelled}


def _run_discovery(job_id: str, queries: list, urls: list, results_per_query: int):
    job = _discovery_jobs[job_id]

    def log(msg):
        job["logs"].append({"ts": datetime.now(timezone.utc).isoformat(), "msg": msg})
        logger.info(f"[job:{job_id[:8]}] {msg}")

    def cancel_check():
        return job.get("cancelled", False)

    log("Discovery job started.")
    try:
        from outbound_crawler import discover_leads
        api_key = os.getenv("GEMINI_API_KEY", "")
        contacted = get_contacted_domains()

        new_leads = discover_leads(
            queries=queries,
            urls=urls,
            results_per_query=results_per_query,
            contacted_domains=contacted,
            api_key=api_key,
            log_fn=log,
            cancel_check=cancel_check,
        )

        if not job.get("cancelled"):
            # Merge into leads.json (deduplicate by domain)
            existing = load_leads()
            existing_domains = {l["domain"] for l in existing}
            added = 0
            for lead in new_leads:
                if lead["domain"] not in existing_domains:
                    existing.append(lead)
                    existing_domains.add(lead["domain"])
                    added += 1
            save_leads(existing)
            job["leads_found"] = added
            log(f"Discovery complete. {added} new leads added.")
        job["status"] = "completed"
    except Exception as e:
        logger.exception("Discovery job failed")
        job["status"] = "failed"
        job["error"] = str(e)
        job["logs"].append({"ts": datetime.now(timezone.utc).isoformat(), "msg": f"[ERROR] {e}"})


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Auto-resume AutoPilot on startup if it was previously enabled."""
    cfg = ap_load_config()
    if cfg.get("enabled", False):
        logger.info("AutoPilot was enabled — resuming automatically.")
        autopilot.start()
    yield  # server is running here
    # Clean shutdown
    autopilot.stop()

app = FastAPI(title="AI Sales Outbound Dialer", version="1.0.0", lifespan=lifespan)

# ---------------------------------------------------------------------------
# Auto-Pilot initialisation (done after app is created so call_lead is available)
# ---------------------------------------------------------------------------
from outbound_autopilot import AutoPilot, load_config as ap_load_config, save_config as ap_save_config, load_log as ap_load_log

def _ap_call_lead(lead_id: str) -> dict:
    """Wrapper used by AutoPilot to call a lead without going through HTTP."""
    from outbound_caller import trigger_call, build_default_prompt
    settings = get_settings()
    leads = load_leads()
    lead = next((l for l in leads if l["id"] == lead_id), None)
    if not lead:
        return {"success": False, "error": "Lead not found"}
    if not lead.get("phoneNumber"):
        return {"success": False, "error": "No phone number"}
    override_mode = settings.get("OVERRIDE_SYSTEM_PROMPT", "false").lower() == "true"
    custom_prompt = build_default_prompt(
        business_name=lead["businessName"],
        industry=lead["industry"],
        services=lead["services"],
        pain_points=lead["painPoints"],
    ) if override_mode else None
    result = trigger_call(
        phone_number=lead["phoneNumber"],
        assistant_id=settings["VAPI_ASSISTANT_ID"],
        phone_number_id=settings["VAPI_PHONE_NUMBER_ID"],
        api_key=settings["VAPI_API_KEY"],
        business_name=lead["businessName"],
        industry=lead["industry"],
        services=lead["services"],
        pain_points=lead["painPoints"],
        override_system_prompt=override_mode,
        custom_system_prompt=custom_prompt,
    )
    if result["success"]:
        now = datetime.now(timezone.utc).isoformat()
        for i, l in enumerate(leads):
            if l["id"] == lead_id:
                leads[i]["lastCalledAt"] = now
                leads[i]["callCount"] = leads[i].get("callCount", 0) + 1
                leads[i]["callHistory"].append({"vapi_call_id": result.get("call_id"), "calledAt": now, "outcome": "pending"})
        save_leads(leads)
        calls = load_calls()
        calls.insert(0, {
            "id": str(uuid.uuid4()),
            "vapi_call_id": result.get("call_id"),
            "lead_id": lead_id,
            "businessName": lead["businessName"],
            "phoneNumber": lead["phoneNumber"],
            "status": "initiated",
            "outcome": None,
            "duration": None,
            "startedAt": now,
            "endedAt": None,
            "error": None,
            "source": "autopilot",
        })
        save_calls(calls)
    return result

autopilot = AutoPilot(
    leads_fn=load_leads,
    save_leads_fn=save_leads,
    call_lead_fn=_ap_call_lead,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Block unauthenticated access to all /api/* routes except /api/auth/* and /webhook."""
    path = request.url.path
    # Always allow: static files, root dashboard, auth endpoints, webhook
    open_paths = ("/", "/webhook", "/docs", "/openapi.json", "/redoc")
    if (
        path in open_paths
        or path.startswith("/static")
        or path.startswith("/api/auth")
    ):
        return await call_next(request)

    # All other /api/* routes require a valid session
    if path.startswith("/api") and not _check_session(request):
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})

    return await call_next(request)


# ── Auth endpoints ───────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login")
def login(req: LoginRequest, response: Response):
    pw_hash = hashlib.sha256(req.password.encode()).hexdigest()
    if req.username != _AUTH_USERNAME or pw_hash != _AUTH_PASSWORD_HASH:
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    token = secrets.token_hex(32)
    _SESSIONS.add(token)
    response.set_cookie(
        key=_SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=86400 * 7,  # 7 days
    )
    return {"success": True}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(_SESSION_COOKIE)
    if token:
        _SESSIONS.discard(token)
    response.delete_cookie(_SESSION_COOKIE)
    return {"success": True}


@app.get("/api/auth/check")
def auth_check(request: Request):
    if _check_session(request):
        return {"authenticated": True}
    return JSONResponse(status_code=401, content={"authenticated": False})


# Serve static dashboard files
STATIC_DIR = BASE_DIR / "outbound_static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def serve_dashboard():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {"status": "online", "message": "Dashboard UI not found. Place outbound_static/index.html here."}


# ---------------------------------------------------------------------------
# Lead Discovery
# ---------------------------------------------------------------------------

class DiscoverRequest(BaseModel):
    queries: Optional[List[str]] = Field(default=None, description="Search query strings")
    urls: Optional[List[str]] = Field(default=None, description="Direct website URLs to process")
    results_per_query: int = Field(default=8, ge=1, le=20)


@app.post("/api/leads/discover")
def start_discovery(req: DiscoverRequest, background_tasks: BackgroundTasks):
    if not req.queries and not req.urls:
        raise HTTPException(400, "Provide at least one query or URL.")
    job_id = str(uuid.uuid4())
    _discovery_jobs[job_id] = {
        "id": job_id,
        "status": "running",
        "logs": [],
        "leads_found": 0,
        "cancelled": False,
        "error": None,
    }
    background_tasks.add_task(_run_discovery, job_id, req.queries or [], req.urls or [], req.results_per_query)
    return {"job_id": job_id, "status": "running"}


@app.get("/api/leads/status/{job_id}")
def get_discovery_status(job_id: str):
    if job_id not in _discovery_jobs:
        raise HTTPException(404, "Job not found.")
    return _discovery_jobs[job_id]


@app.post("/api/leads/status/{job_id}/cancel")
def cancel_discovery(job_id: str):
    if job_id not in _discovery_jobs:
        raise HTTPException(404, "Job not found.")
    _discovery_jobs[job_id]["cancelled"] = True
    return {"message": "Cancellation requested."}


# ---------------------------------------------------------------------------
# Leads CRUD
# ---------------------------------------------------------------------------

@app.get("/api/leads")
def list_leads(status: Optional[str] = None):
    leads = load_leads()
    if status:
        leads = [l for l in leads if l.get("status") == status]
    # Sort by addedAt descending
    leads.sort(key=lambda l: l.get("addedAt", ""), reverse=True)
    return leads


class AddLeadRequest(BaseModel):
    businessName: str
    industry: Optional[str] = ""
    services: Optional[str] = ""
    painPoints: Optional[str] = ""
    phoneNumber: str
    url: Optional[str] = ""
    domain: Optional[str] = ""


@app.post("/api/leads/add")
def add_lead_manually(req: AddLeadRequest):
    from urllib.parse import urlparse
    from outbound_crawler import normalise_au_phone

    domain = req.domain or (urlparse(req.url).netloc.lstrip("www.") if req.url else "manual")
    phone = normalise_au_phone(req.phoneNumber) if req.phoneNumber else ""

    leads = load_leads()
    # Check for duplicate domain
    if any(l["domain"] == domain for l in leads):
        raise HTTPException(409, f"A lead with domain '{domain}' already exists.")

    lead = {
        "id": str(uuid.uuid4()),
        "domain": domain,
        "url": req.url or "",
        "businessName": req.businessName,
        "industry": req.industry or "",
        "services": req.services or "",
        "painPoints": req.painPoints or "",
        "phoneNumber": phone,
        "status": "new",
        "callCount": 0,
        "lastCalledAt": None,
        "lastCallOutcome": None,
        "callHistory": [],
        "addedAt": datetime.now(timezone.utc).isoformat(),
    }
    leads.append(lead)
    save_leads(leads)
    return lead


@app.put("/api/leads/{lead_id}")
def update_lead(lead_id: str, updates: dict):
    from outbound_crawler import normalise_au_phone
    leads = load_leads()
    for i, l in enumerate(leads):
        if l["id"] == lead_id:
            protected = {"id", "domain", "addedAt", "callHistory", "callCount"}
            for k, v in updates.items():
                if k not in protected:
                    leads[i][k] = v
            if "phoneNumber" in updates:
                leads[i]["phoneNumber"] = normalise_au_phone(updates["phoneNumber"])
            save_leads(leads)
            return leads[i]
    raise HTTPException(404, "Lead not found.")


@app.patch("/api/leads/{lead_id}/status")
def set_lead_status(lead_id: str, body: dict):
    valid_statuses = {"new", "no_answer", "contacted", "do_not_call"}
    new_status = body.get("status")
    if new_status not in valid_statuses:
        raise HTTPException(400, f"Invalid status. Must be one of: {valid_statuses}")
    leads = load_leads()
    for i, l in enumerate(leads):
        if l["id"] == lead_id:
            leads[i]["status"] = new_status
            save_leads(leads)
            return leads[i]
    raise HTTPException(404, "Lead not found.")


@app.delete("/api/leads/{lead_id}")
def delete_lead(lead_id: str):
    leads = load_leads()
    new_list = [l for l in leads if l["id"] != lead_id]
    if len(new_list) == len(leads):
        raise HTTPException(404, "Lead not found.")
    save_leads(new_list)
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Outbound Calling
# ---------------------------------------------------------------------------

@app.post("/api/leads/{lead_id}/call")
def call_lead(lead_id: str):
    from outbound_caller import trigger_call, build_default_prompt

    settings = get_settings()
    api_key = settings["VAPI_API_KEY"]
    assistant_id = settings["VAPI_ASSISTANT_ID"]
    phone_number_id = settings["VAPI_PHONE_NUMBER_ID"]

    if not api_key:
        raise HTTPException(400, "VAPI_API_KEY is not configured. Go to Settings.")
    if not assistant_id:
        raise HTTPException(400, "VAPI_ASSISTANT_ID is not configured. Go to Settings.")
    if not phone_number_id:
        raise HTTPException(400, "VAPI_PHONE_NUMBER_ID is not configured. Go to Settings.")

    leads = load_leads()
    lead = next((l for l in leads if l["id"] == lead_id), None)
    if not lead:
        raise HTTPException(404, "Lead not found.")
    if not lead.get("phoneNumber"):
        raise HTTPException(400, "This lead has no phone number.")
    if lead.get("status") == "do_not_call":
        raise HTTPException(403, "This lead is marked Do Not Call.")

    override_mode = settings.get("OVERRIDE_SYSTEM_PROMPT", "false").lower() == "true"
    custom_prompt = build_default_prompt(
        business_name=lead["businessName"],
        industry=lead["industry"],
        services=lead["services"],
        pain_points=lead["painPoints"],
    ) if override_mode else None

    result = trigger_call(
        phone_number=lead["phoneNumber"],
        assistant_id=assistant_id,
        phone_number_id=phone_number_id,
        api_key=api_key,
        business_name=lead["businessName"],
        industry=lead["industry"],
        services=lead["services"],
        pain_points=lead["painPoints"],
        override_system_prompt=override_mode,
        custom_system_prompt=custom_prompt,
    )

    now = datetime.now(timezone.utc).isoformat()

    # Log the call attempt
    call_entry = {
        "id": str(uuid.uuid4()),
        "vapi_call_id": result.get("call_id"),
        "lead_id": lead_id,
        "businessName": lead["businessName"],
        "phoneNumber": lead["phoneNumber"],
        "status": "initiated" if result["success"] else "failed",
        "outcome": None,
        "duration": None,
        "startedAt": now,
        "endedAt": None,
        "error": result.get("error"),
    }
    calls = load_calls()
    calls.insert(0, call_entry)
    save_calls(calls)

    if result["success"]:
        # Update lead — mark as "calling" momentarily; webhook will update final status
        for i, l in enumerate(leads):
            if l["id"] == lead_id:
                leads[i]["lastCalledAt"] = now
                leads[i]["callCount"] = leads[i].get("callCount", 0) + 1
                leads[i]["callHistory"].append({
                    "vapi_call_id": result.get("call_id"),
                    "calledAt": now,
                    "outcome": "pending",
                })
        save_leads(leads)

    return {
        "success": result["success"],
        "call_id": result.get("call_id"),
        "call_log_id": call_entry["id"],
        "error": result.get("error"),
    }


# ---------------------------------------------------------------------------
# Vapi Webhook (call outcome receiver)
# ---------------------------------------------------------------------------

try:
    from vapi_webhook import process_vapi_tool_calls, process_webhook_payload
except ImportError:
    process_vapi_tool_calls = None
    process_webhook_payload = None

@app.post("/webhook")
async def vapi_webhook(request: Request, background_tasks: BackgroundTasks):
    """Receives Vapi notifications (tool calls & end-of-call-reports)."""
    try:
        payload = await request.json()
    except Exception:
        return Response(content='{"error":"invalid json"}', status_code=400, media_type="application/json")

    message = payload.get("message", {}) if isinstance(payload, dict) else {}
    event_type = message.get("type", "")

    # Handle Tool Calls (Google Calendar Availability, Booking, Email Tool)
    if event_type == "tool-calls" or "toolCalls" in message or "toolCallList" in message:
        if process_vapi_tool_calls:
            try:
                return process_vapi_tool_calls(payload)
            except Exception as e:
                logger.error(f"Error executing tool calls: {e}")
                return {"results": [{"toolCallId": tc.get("id"), "result": f"Error: {str(e)}"} for tc in (message.get("toolCalls") or [])]}
        else:
            return {"results": []}

    # Handle End of Call Report (Lead Status Update & Email Summary)
    if event_type == "end-of-call-report":
        call = message.get("call", {})
        vapi_call_id = call.get("id")
        ended_reason = call.get("endedReason", "")
        duration = call.get("duration", 0) or 0

        from outbound_caller import classify_call_outcome
        outcome = classify_call_outcome(ended_reason, duration)

        now = datetime.now(timezone.utc).isoformat()

        # Update call log
        calls = load_calls()
        for i, c in enumerate(calls):
            if c.get("vapi_call_id") == vapi_call_id:
                calls[i]["status"] = "completed"
                calls[i]["outcome"] = outcome
                calls[i]["duration"] = duration
                calls[i]["endedAt"] = now
                lead_id = c.get("lead_id")
                break
        else:
            lead_id = None
        save_calls(calls)

        # Update lead status
        if lead_id:
            leads = load_leads()
            for i, l in enumerate(leads):
                if l["id"] == lead_id:
                    leads[i]["status"] = outcome
                    leads[i]["lastCallOutcome"] = ended_reason
                    # Update history entry
                    for j, h in enumerate(leads[i].get("callHistory", [])):
                        if h.get("vapi_call_id") == vapi_call_id:
                            leads[i]["callHistory"][j]["outcome"] = outcome
                            leads[i]["callHistory"][j]["endedReason"] = ended_reason
                            leads[i]["callHistory"][j]["duration"] = duration
                            break
                    break
            save_leads(leads)

        logger.info(f"Webhook: call {vapi_call_id} ended with reason={ended_reason!r} → outcome={outcome}")
        
        # Trigger email report in background via vapi_webhook module
        if process_webhook_payload:
            background_tasks.add_task(process_webhook_payload, payload)

        return {"status": "processed", "outcome": outcome}

    return {"status": "ignored", "type": event_type}


# ---------------------------------------------------------------------------
# Call Log
# ---------------------------------------------------------------------------

@app.get("/api/calls")
def get_calls(limit: int = 50):
    calls = load_calls()
    return calls[:limit]


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@app.get("/api/settings")
def get_settings_endpoint():
    s = get_settings()
    # Mask API keys for security
    masked = {}
    for k, v in s.items():
        if ("KEY" in k or "JSON" in k or "SECRET" in k) and v:
            masked[k] = v[:6] + "..." + v[-4:] if len(v) > 10 else "***"
        else:
            masked[k] = v
    return masked


@app.get("/api/settings/raw")
def get_settings_raw():
    """Returns full unmasked settings (for pre-filling forms)."""
    return get_settings()


@app.post("/api/settings")
def update_settings_endpoint(body: dict):
    update_settings(body)
    return {"status": "saved"}


# ---------------------------------------------------------------------------
# Auto-Pilot API endpoints
# ---------------------------------------------------------------------------

@app.get("/api/autopilot/state")
def get_autopilot_state():
    """Returns real-time autopilot status, counters, and recent log."""
    return autopilot.get_state()


@app.get("/api/autopilot/config")
def get_autopilot_config():
    return ap_load_config()


@app.post("/api/autopilot/config")
def update_autopilot_config(body: dict):
    """Save autopilot settings (queries, intervals, limits etc.)."""
    cfg = ap_load_config()
    allowed = [
        "enabled", "search_queries", "results_per_query",
        "discover_interval_hours", "call_delay_seconds",
        "daily_call_limit", "retry_after_hours",
        "calling_hours_start", "calling_hours_end", "calling_days",
    ]
    for k, v in body.items():
        if k in allowed:
            cfg[k] = v
    ap_save_config(cfg)
    return cfg


@app.post("/api/autopilot/start")
def start_autopilot():
    cfg = ap_load_config()
    cfg["enabled"] = True
    ap_save_config(cfg)
    autopilot.start()
    return {"status": "started"}


@app.post("/api/autopilot/stop")
def stop_autopilot():
    cfg = ap_load_config()
    cfg["enabled"] = False
    ap_save_config(cfg)
    autopilot.stop()
    return {"status": "stopped"}


@app.get("/api/autopilot/log")
def get_autopilot_log(limit: int = 100):
    """Returns persisted full autopilot activity log."""
    log = ap_load_log()
    return log[-limit:]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.getenv("OUTBOUND_PORT", "8080"))
    logger.info(f"Starting AI Sales Outbound Dialer on port {port}")
    uvicorn.run("outbound_server:app", host="0.0.0.0", port=port, reload=True)
