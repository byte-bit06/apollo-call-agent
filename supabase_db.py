import os
import json
import logging
import requests
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("supabase_db")

# Read environment variables
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://bcsyozvmtwgwstgvnfhf.supabase.co").strip().rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "sb_secret_YzZGrG8E8oCKRNdjTHL_cw_aY_SSD0m").strip()

# Local File Path fallbacks (for resilience)
BASE_DIR = Path(__file__).parent.resolve()
LEADS_FILE = BASE_DIR / "leads.json"
CALLS_FILE = BASE_DIR / "call_log.json"
LOG_FILE = BASE_DIR / "autopilot_log.json"

def get_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

def _load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default

def _save_json(path: Path, data):
    try:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.error(f"Local save failed: {e}")

# ─── Leads Table CRUD Operations ───

def db_load_leads() -> list:
    """Fetch all leads from Supabase, falling back to local leads.json if error."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.warning("Supabase URL or Key not set. Falling back to local file.")
        return _load_json(LEADS_FILE, [])
    
    try:
        url = f"{SUPABASE_URL}/rest/v1/leads"
        headers = get_headers()
        # Fetch ordered by addedAt
        params = {"order": "addedAt.desc"}
        response = requests.get(url, headers=headers, params=params, timeout=10)
        
        if response.status_code == 200:
            leads = response.json()
            # Cache locally to keep backup sync'd
            _save_json(LEADS_FILE, leads)
            return leads
        else:
            logger.error(f"Supabase GET leads failed: {response.status_code} {response.text}")
    except Exception as e:
        logger.error(f"Supabase GET leads failed with exception: {e}")
        
    return _load_json(LEADS_FILE, [])

def db_save_leads(leads: list):
    """Upsert all leads to Supabase, falling back to local leads.json if error."""
    # Write locally first so we always have a local backup
    _save_json(LEADS_FILE, leads)
    
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
        
    try:
        url = f"{SUPABASE_URL}/rest/v1/leads"
        headers = get_headers()
        # PostgREST upsert header
        headers["Prefer"] = "resolution=merge-duplicates,return=representation"
        
        # Prepare list for clean upsert
        cleaned_leads = []
        for lead in leads:
            cleaned = dict(lead)
            # Ensure UUID format is correct, or delete to let Supabase generate it
            if "id" in cleaned and not cleaned["id"]:
                del cleaned["id"]
            # Ensure defaults
            if "callHistory" in cleaned and cleaned["callHistory"] is None:
                cleaned["callHistory"] = []
            cleaned_leads.append(cleaned)
            
        if cleaned_leads:
            response = requests.post(url, headers=headers, json=cleaned_leads, timeout=10)
            if response.status_code not in (200, 201):
                logger.error(f"Supabase POST leads failed: {response.status_code} {response.text}")
    except Exception as e:
        logger.error(f"Supabase POST leads failed with exception: {e}")

# ─── Calls Table Operations ───

def db_load_calls() -> list:
    """Fetch calls from Supabase, falling back to local call_log.json."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return _load_json(CALLS_FILE, [])
        
    try:
        url = f"{SUPABASE_URL}/rest/v1/calls"
        headers = get_headers()
        params = {"order": "startedAt.desc"}
        response = requests.get(url, headers=headers, params=params, timeout=10)
        
        if response.status_code == 200:
            calls = response.json()
            _save_json(CALLS_FILE, calls)
            return calls
        else:
            logger.error(f"Supabase GET calls failed: {response.status_code} {response.text}")
    except Exception as e:
        logger.error(f"Supabase GET calls failed with exception: {e}")
        
    return _load_json(CALLS_FILE, [])

def db_save_calls(calls: list):
    """Upsert all calls to Supabase, falling back to local call_log.json if error."""
    _save_json(CALLS_FILE, calls)
    
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
        
    try:
        url = f"{SUPABASE_URL}/rest/v1/calls"
        headers = get_headers()
        headers["Prefer"] = "resolution=merge-duplicates,return=representation"
        
        cleaned_calls = []
        for call in calls:
            cleaned = dict(call)
            if "id" in cleaned and not cleaned["id"]:
                del cleaned["id"]
            cleaned_calls.append(cleaned)
            
        if cleaned_calls:
            response = requests.post(url, headers=headers, json=cleaned_calls, timeout=10)
            if response.status_code not in (200, 201):
                logger.error(f"Supabase POST calls failed: {response.status_code} {response.text}")
    except Exception as e:
        logger.error(f"Supabase POST calls failed with exception: {e}")

def db_add_call_log(call_record: dict):
    """Insert a call record into Supabase, falling back to local call_log.json."""
    # Append locally first
    local_calls = _load_json(CALLS_FILE, [])
    local_calls.append(call_record)
    _save_json(CALLS_FILE, local_calls)
    
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
        
    try:
        url = f"{SUPABASE_URL}/rest/v1/calls"
        headers = get_headers()
        
        # PostgREST insert
        response = requests.post(url, headers=headers, json=call_record, timeout=10)
        if response.status_code not in (200, 201):
            logger.error(f"Supabase POST call failed: {response.status_code} {response.text}")
    except Exception as e:
        logger.error(f"Supabase POST call failed with exception: {e}")

# ─── Autopilot Logs Table Operations ───

def db_load_autopilot_logs() -> list:
    """Fetch autopilot logs from Supabase, falling back to local autopilot_log.json."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return _load_json(LOG_FILE, [])
        
    try:
        url = f"{SUPABASE_URL}/rest/v1/autopilot_logs"
        headers = get_headers()
        params = {"order": "ts.desc", "limit": "500"}
        response = requests.get(url, headers=headers, params=params, timeout=10)
        
        if response.status_code == 200:
            logs = response.json()
            # Map back to client format (descending ts)
            logs.reverse() # dashboard expects oldest first for appending log tail
            _save_json(LOG_FILE, logs)
            return logs
        else:
            logger.error(f"Supabase GET autopilot_logs failed: {response.status_code} {response.text}")
    except Exception as e:
        logger.error(f"Supabase GET autopilot_logs failed with exception: {e}")
        
    return _load_json(LOG_FILE, [])

def db_add_autopilot_log(msg: str, level: str = "info"):
    """Insert autopilot log into Supabase, falling back to local autopilot_log.json."""
    # Log entry format
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "msg": msg,
        "level": level
    }
    
    # Save locally
    local_logs = _load_json(LOG_FILE, [])
    local_logs.append(entry)
    _save_json(LOG_FILE, local_logs[-500:])
    
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
        
    try:
        url = f"{SUPABASE_URL}/rest/v1/autopilot_logs"
        headers = get_headers()
        
        response = requests.post(url, headers=headers, json=entry, timeout=10)
        if response.status_code not in (200, 201):
            logger.error(f"Supabase POST autopilot_log failed: {response.status_code} {response.text}")
    except Exception as e:
        logger.error(f"Supabase POST autopilot_log failed with exception: {e}")
