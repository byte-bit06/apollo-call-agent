"""
outbound_autopilot.py
Autonomous Auto-Pilot for the AI Sales Outbound Dialer.

Runs as a background thread inside outbound_server.py.
When enabled it:
  1. Periodically discovers new Australian business leads via DuckDuckGo + Gemini.
  2. Automatically calls every new lead (with a configurable delay between calls).
  3. Retries 'no_answer' leads after a configurable cooldown period.
  4. Respects a daily call cap to avoid blowing Vapi usage limits.
  5. Logs everything to an in-memory activity log (also persisted to autopilot_log.json).
"""

import os
import json
import time
import threading
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger("autopilot")

BASE_DIR = Path(__file__).parent.resolve()
CONFIG_FILE = BASE_DIR / "autopilot_config.json"
LOG_FILE = BASE_DIR / "autopilot_log.json"
CALL_TRACKER_FILE = BASE_DIR / "autopilot_calls_today.json"

# ─────────────────────────────────────────────
# Default configuration
# ─────────────────────────────────────────────

DEFAULT_CONFIG = {
    "enabled": False,
    "search_queries": [
        "RTO registered training organisations",
        "accounting firms",
        "lawyer firms",
        "marketing agencies",
        "recruitment agencies",
        "real estate agencies",
        "IT support companies",
        "commercial builders",
    ],
    "results_per_query": 5,
    "discover_interval_hours": 2,
    "call_delay_seconds": 45,
    "daily_call_limit": 50,
    "retry_after_hours": 24,
    "calling_hours_start": 9,
    "calling_hours_end": 17,
    "calling_days": [0, 1, 2, 3, 4],  # Mon=0 .. Fri=4
}


# ─────────────────────────────────────────────
# Config & Log persistence
# ─────────────────────────────────────────────

def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            cfg = dict(DEFAULT_CONFIG)
            cfg.update(saved)
            return cfg
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def load_log() -> list:
    if LOG_FILE.exists():
        try:
            data = json.loads(LOG_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data[-500:]  # keep last 500 entries
        except Exception:
            pass
    return []


def append_log_entry(entry: dict):
    log = load_log()
    log.append(entry)
    LOG_FILE.write_text(json.dumps(log[-500:], indent=2, ensure_ascii=False), encoding="utf-8")


def get_calls_today_count() -> int:
    """Returns how many calls have been made today (UTC date)."""
    today = datetime.now(timezone.utc).date().isoformat()
    if CALL_TRACKER_FILE.exists():
        try:
            data = json.loads(CALL_TRACKER_FILE.read_text(encoding="utf-8"))
            if data.get("date") == today:
                return data.get("count", 0)
        except Exception:
            pass
    return 0


def increment_calls_today():
    today = datetime.now(timezone.utc).date().isoformat()
    count = get_calls_today_count() + 1
    CALL_TRACKER_FILE.write_text(json.dumps({"date": today, "count": count}), encoding="utf-8")


# ─────────────────────────────────────────────
# Auto-Pilot Engine
# ─────────────────────────────────────────────

class AutoPilot:
    """
    Background thread that runs the full discover → call pipeline autonomously.
    """

    def __init__(self, leads_fn, save_leads_fn, call_lead_fn):
        """
        Args:
            leads_fn:       Callable() → list of all lead dicts.
            save_leads_fn:  Callable(leads) → None.
            call_lead_fn:   Callable(lead_id) → {"success": bool, "call_id": str, "error": str}.
        """
        self.leads_fn = leads_fn
        self.save_leads_fn = save_leads_fn
        self.call_lead_fn = call_lead_fn

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # In-memory state (also readable via API)
        self.status = "stopped"          # stopped | running | paused | calling | discovering
        self.current_action = "Idle"
        self.next_discovery_at: str | None = None
        self.calls_today = 0
        self.total_calls_session = 0
        self.total_leads_discovered_session = 0
        self.started_at: str | None = None
        self.recent_logs: list = []      # last 100 log entries (in memory)

    # ── Public control methods ───────────────

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="AutoPilot")
        self._thread.start()
        self.status = "running"
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._log("🚀 Auto-Pilot started.", level="success")

    def stop(self):
        self._stop_event.set()
        self.status = "stopped"
        self.current_action = "Idle"
        self._log("⏹️ Auto-Pilot stopped.", level="warning")

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def get_state(self) -> dict:
        return {
            "status": self.status,
            "current_action": self.current_action,
            "next_discovery_at": self.next_discovery_at,
            "calls_today": get_calls_today_count(),
            "calls_today_limit": load_config().get("daily_call_limit", 50),
            "total_calls_session": self.total_calls_session,
            "total_leads_discovered_session": self.total_leads_discovered_session,
            "started_at": self.started_at,
            "recent_logs": self.recent_logs[-50:],
        }

    # ── Internal helpers ─────────────────────

    def _log(self, msg: str, level: str = "info"):
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "msg": msg,
            "level": level,
        }
        self.recent_logs.append(entry)
        if len(self.recent_logs) > 200:
            self.recent_logs = self.recent_logs[-100:]
        append_log_entry(entry)
        logger.info(f"[AutoPilot] {msg}")

    def _should_stop(self) -> bool:
        return self._stop_event.is_set()

    def _is_within_calling_hours(self, cfg: dict) -> bool:
        """Returns True if current local hour is within configured calling window."""
        now_local = datetime.now()  # local system time
        hour = now_local.hour
        weekday = now_local.weekday()
        calling_days = cfg.get("calling_days", list(range(5)))
        start = cfg.get("calling_hours_start", 9)
        end = cfg.get("calling_hours_end", 17)
        return weekday in calling_days and start <= hour < end

    def _wait_sleep(self, seconds: float):
        """Sleep in small intervals to allow responsive stopping."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self._should_stop():
                return
            time.sleep(min(2.0, end - time.monotonic()))

    # ── Main loop ────────────────────────────

    def _run(self):
        """Main auto-pilot loop."""
        cfg = load_config()
        last_discovery_at = None

        while not self._should_stop():
            cfg = load_config()  # re-read config each cycle to pick up live changes

            if not cfg.get("enabled", False):
                self.status = "paused"
                self.current_action = "Auto-Pilot disabled in config"
                self._wait_sleep(10)
                continue

            # ── Step 1: Discover new leads if it's time or we are out of leads ──
            now = datetime.now(timezone.utc)
            interval_hours = cfg.get("discover_interval_hours", 2)
            
            # Check uncalled leads backlog (leads with status == 'new' and a valid number)
            all_leads = self.leads_fn()
            uncalled_leads = [l for l in all_leads if l.get("status") == "new" and l.get("phoneNumber")]
            backlog_limit_reached = len(uncalled_leads) >= 20
            
            out_of_leads = len(uncalled_leads) == 0
            time_since_last_discovery = (now - last_discovery_at).total_seconds() if last_discovery_at else 999999
            
            # Trigger discovery if:
            # 1. We've never done it (last_discovery_at is None)
            # 2. We are out of leads to call AND at least 5 minutes (300s) has passed since last discovery
            # 3. The scheduled interval has passed
            due_for_discovery = (
                last_discovery_at is None
                or (out_of_leads and time_since_last_discovery >= 300)
                or time_since_last_discovery >= interval_hours * 3600
            )

            # If uncalled leads backlog is 20 or more, hold discovery!
            if backlog_limit_reached:
                due_for_discovery = False
                self.current_action = f"Discovery paused: queue has {len(uncalled_leads)} uncalled leads."
                # Log once in a while
                if now.second == 0 or last_discovery_at is None:
                    self._log(f"ℹ️ Lead discovery paused: uncalled leads backlog ({len(uncalled_leads)}) is >= 20. Scanning calling queue first.", level="info")

            if due_for_discovery:
                if out_of_leads and last_discovery_at is not None:
                    self._log("ℹ️ Out of new leads in queue. Triggering early discovery cycle to find more.", level="info")
                self.status = "discovering"
                self.current_action = "Discovering new leads..."
                new_count = self._run_discovery(cfg)
                self.total_leads_discovered_session += new_count
                last_discovery_at = datetime.now(timezone.utc)
                next_dt = last_discovery_at + timedelta(hours=interval_hours)
                self.next_discovery_at = next_dt.isoformat()

                if self._should_stop():
                    break

            # ── Step 2: Call new and eligible callback leads ──
            self.status = "running"
            self.current_action = "Scanning for leads to call..."
            self._run_calling_cycle(cfg)

            if self._should_stop():
                break

            # ── Step 3: Wait until next cycle check (60s) ──
            time_until_scheduled = max(0, int((last_discovery_at + timedelta(hours=interval_hours) - datetime.now(timezone.utc)).total_seconds())) if last_discovery_at else 0
            next_desc = f"{time_until_scheduled // 60}m remaining" if time_until_scheduled > 0 else "Due"
            self.current_action = f"Waiting... Next scheduled discovery: {next_desc} (or early if out of leads)"
            self._wait_sleep(60)

        self.status = "stopped"
        self.current_action = "Idle"

    def _run_discovery(self, cfg: dict) -> int:
        """Runs the lead discovery pipeline and returns count of newly added leads."""
        from outbound_crawler import discover_leads

        base_queries = cfg.get("search_queries", [])
        results_per_query = cfg.get("results_per_query", 5)

        if not base_queries:
            self._log("⚠️ No search queries configured. Go to Auto-Pilot tab and add search queries.", level="warning")
            return 0

        # Expand each base query across AU cities so we always have fresh results
        AU_CITIES = [
            "Sydney", "Melbourne", "Brisbane", "Perth", "Adelaide",
            "Gold Coast", "Canberra", "Newcastle", "Hobart", "Darwin",
            "Geelong", "Townsville", "Sunshine Coast", "Cairns", "Wollongong",
        ]

        # Remove any known cities or location terms from the query so we can cleanly target new cities
        all_location_terms = {c.lower() for c in AU_CITIES} | {
            "australia", "australian", "nsw", "vic", "qld", "wa", "sa", "tas", "nt", "act",
            "sydney", "melbourne", "brisbane", "perth", "adelaide", "gold coast", "canberra", 
            "newcastle", "hobart", "darwin", "geelong", "townsville", "sunshine coast", "cairns", "wollongong"
        }

        # Build a full rotation: each base query × each city
        queries_this_run = []
        offset = (datetime.now(timezone.utc).minute // 5) % len(AU_CITIES)

        for q in base_queries:
            # Clean up the query by removing location words
            words = q.split()
            cleaned_words = [w for w in words if w.strip(",.()").lower() not in all_location_terms]
            clean = " ".join(cleaned_words)
            if not clean:
                clean = q # fallback
            
            for i in range(len(AU_CITIES)):
                city = AU_CITIES[(i + offset) % len(AU_CITIES)]
                queries_this_run.append(f"{clean} {city} Australia")

        # Limit to a reasonable number per run (avoid hitting DDG too hard)
        queries_this_run = queries_this_run[:len(base_queries) * 3]

        self._log(f"🔍 Starting discovery: {len(queries_this_run)} search variations, {results_per_query} results each...", level="info")
        self.current_action = "Running DuckDuckGo searches..."

        # Avoid re-contacting already-contacted domains
        all_leads = self.leads_fn()
        # Also skip all domains already in the system (not just contacted)
        existing_domains = {l["domain"] for l in all_leads}
        contacted_domains = {
            l["domain"]
            for l in all_leads
            if l.get("status") in ("contacted", "do_not_call")
        }

        api_key = os.getenv("GEMINI_API_KEY", "")

        new_leads = discover_leads(
            queries=queries_this_run,
            urls=[],
            results_per_query=results_per_query,
            contacted_domains=existing_domains,  # skip ALL known domains, not just contacted
            api_key=api_key,
            log_fn=self._log,
            cancel_check=self._should_stop,
        )

        if not new_leads:
            self._log("ℹ️ No new leads this cycle — all discovered sites are already in the system. Will try again next cycle with different cities.", level="info")
            return 0

        # Merge into leads store (deduplicate by domain)
        existing = self.leads_fn()
        existing_domains = {l["domain"] for l in existing}
        added = 0
        for lead in new_leads:
            if lead["domain"] not in existing_domains:
                existing.append(lead)
                existing_domains.add(lead["domain"])
                added += 1

        self.save_leads_fn(existing)
        self._log(f"✅ Discovery complete. {added} new leads added.", level="success")
        return added

    def _run_calling_cycle(self, cfg: dict):
        """Calls all eligible new and callback leads."""
        if not self._is_within_calling_hours(cfg):
            self.current_action = "Outside calling hours — waiting..."
            self._log("🕐 Outside calling hours. Calls paused.", level="info")
            return

        daily_limit = cfg.get("daily_call_limit", 50)
        calls_today = get_calls_today_count()

        if calls_today >= daily_limit:
            self.current_action = f"Daily limit reached ({calls_today}/{daily_limit}). Waiting until tomorrow."
            self._log(f"🛑 Daily call limit reached ({calls_today}/{daily_limit}).", level="warning")
            return

        retry_after_hours = cfg.get("retry_after_hours", 24)
        call_delay = cfg.get("call_delay_seconds", 45)

        all_leads = self.leads_fn()

        # Leads eligible to be called:
        # 1. Status = 'new' with a phone number
        # 2. Status = 'no_answer' AND last called > retry_after_hours ago
        now = datetime.now(timezone.utc)
        eligible = []

        for lead in all_leads:
            if not lead.get("phoneNumber"):
                continue
            status = lead.get("status", "new")
            if status == "new":
                eligible.append(lead)
            elif status == "no_answer":
                last_called = lead.get("lastCalledAt")
                if last_called:
                    last_dt = datetime.fromisoformat(last_called.replace("Z", "+00:00"))
                    hours_since = (now - last_dt).total_seconds() / 3600
                    if hours_since >= retry_after_hours:
                        eligible.append(lead)

        if not eligible:
            self.current_action = "No eligible leads to call right now."
            return

        self._log(f"📞 Found {len(eligible)} lead(s) to call this cycle.", level="info")

        for lead in eligible:
            if self._should_stop():
                break

            calls_today = get_calls_today_count()
            if calls_today >= daily_limit:
                self._log(f"🛑 Daily limit reached mid-cycle ({calls_today}/{daily_limit}).", level="warning")
                break

            if not self._is_within_calling_hours(cfg):
                self._log("🕐 Left calling hours mid-cycle. Pausing.", level="info")
                break

            name = lead.get("businessName", lead["domain"])
            phone = lead.get("phoneNumber", "")
            self.current_action = f"Calling {name} ({phone})..."
            self._log(f"📞 Calling {name} — {phone}", level="info")

            try:
                result = self.call_lead_fn(lead["id"])
                if result.get("success"):
                    increment_calls_today()
                    self.total_calls_session += 1
                    self._log(f"✅ Call initiated to {name} (call_id: {result.get('call_id')})", level="success")
                else:
                    self._log(f"❌ Call failed for {name}: {result.get('error')}", level="error")
            except Exception as e:
                self._log(f"❌ Error calling {name}: {e}", level="error")

            # Wait between calls
            self.current_action = f"Cooling down {call_delay}s before next call..."
            self._wait_sleep(call_delay)

        self._log(f"📋 Calling cycle complete. Total calls today: {get_calls_today_count()}/{daily_limit}", level="info")
