"""
outbound_caller.py
Vapi Outbound Call Dispatcher for the AI Sales Outbound Dialer.

- Triggers an outbound phone call via Vapi's POST /call/phone endpoint.
- Passes business context (name, industry, services, painPoints) as Vapi
  variableValues so your assistant's system prompt (with {{businessName}} etc.)
  is automatically personalised per lead.
- Optionally overrides the assistant system prompt directly if configured.
"""

import json
import logging
from urllib import request as urllib_request
from urllib.error import HTTPError

logger = logging.getLogger("outbound_caller")

VAPI_BASE_URL = "https://api.vapi.ai"


# ---------------------------------------------------------------------------
# Core call trigger
# ---------------------------------------------------------------------------

def trigger_call(
    phone_number: str,
    assistant_id: str,
    phone_number_id: str,
    api_key: str,
    business_name: str = "",
    industry: str = "",
    services: str = "",
    pain_points: str = "",
    override_system_prompt: bool = False,
    custom_system_prompt: str = None,
) -> dict:
    """
    Triggers an outbound call via Vapi.

    Args:
        phone_number:         Destination number in E.164 format (e.g. +61412345678).
        assistant_id:         Vapi Assistant ID to use for the call.
        phone_number_id:      Vapi Phone Number ID to call from.
        api_key:              Vapi API private key.
        business_name:        Lead business name (used in variableValues).
        industry:             Lead industry (used in variableValues).
        services:             Lead services summary (used in variableValues).
        pain_points:          Lead pain points (used in variableValues).
        override_system_prompt: If True, sends a full system prompt override instead of variableValues.
        custom_system_prompt: Full system prompt to override if override_system_prompt=True.

    Returns:
        Dict with keys: success (bool), call_id (str|None), error (str|None), raw (dict|None).
    """
    if not phone_number:
        return {"success": False, "error": "No phone number provided.", "call_id": None}
    if not assistant_id:
        return {"success": False, "error": "No VAPI_ASSISTANT_ID configured.", "call_id": None}
    if not phone_number_id:
        return {"success": False, "error": "No VAPI_PHONE_NUMBER_ID configured.", "call_id": None}
    if not api_key:
        return {"success": False, "error": "No VAPI_API_KEY configured.", "call_id": None}

    # Build the assistant overrides block
    assistant_overrides = {}

    if override_system_prompt and custom_system_prompt:
        # Full system prompt override
        assistant_overrides["model"] = {
            "messages": [
                {"role": "system", "content": custom_system_prompt}
            ]
        }
    else:
        # Inject variables into Vapi assistant so {{businessName}} etc. are resolved
        import os
        import zoneinfo
        from datetime import datetime
        tz_str = os.getenv("TIMEZONE", "Australia/Melbourne")
        try:
            tz = zoneinfo.ZoneInfo(tz_str)
        except Exception:
            tz = zoneinfo.ZoneInfo("Australia/Melbourne")
        now_local = datetime.now(tz)
        date_str = now_local.strftime("%A, %B %d, %Y")

        assistant_overrides["variableValues"] = {
            "date": date_str,
            "businessName": business_name or "this business",
            "industry": industry or "your industry",
            "services": services or "your services",
            "painPoints": pain_points or "common operational challenges",
        }

    payload = {
        "phoneNumberId": phone_number_id,
        "assistantId": assistant_id,
        "assistantOverrides": assistant_overrides,
        "customer": {
            "number": phone_number,
            "name": business_name or None,
        },
    }

    logger.info(f"Triggering Vapi call to {phone_number} | Business: {business_name!r}")

    try:
        req = urllib_request.Request(
            f"{VAPI_BASE_URL}/call/phone",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            },
            method="POST",
        )
        with urllib_request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            call_id = body.get("id")
            logger.info(f"Vapi call initiated. call_id={call_id}")
            return {"success": True, "call_id": call_id, "error": None, "raw": body}

    except HTTPError as e:
        try:
            detail = e.read().decode("utf-8")
        except Exception:
            detail = str(e)
        logger.error(f"Vapi HTTP error {e.code}: {detail}")
        return {"success": False, "call_id": None, "error": f"HTTP {e.code}: {detail}", "raw": None}

    except Exception as e:
        logger.error(f"Vapi call failed: {e}")
        return {"success": False, "call_id": None, "error": str(e), "raw": None}


# ---------------------------------------------------------------------------
# Default system prompt builder (used when override mode is on but no
# custom prompt has been specified in settings)
# ---------------------------------------------------------------------------

DEFAULT_SYSTEM_PROMPT_TEMPLATE = """You are a professional AI sales assistant calling on behalf of an AI automation agency based in Australia.

Today's date is {date}. Only suggest or accept booking dates on or after today. When calling check_calendar_availability.

Business Name: {businessName}

Industry: {industry}

Services:
{services}

Common Business Challenges:
{painPoints}

Your goal is to:
1. Introduce yourself warmly and naturally — do NOT sound like a robot.
2. Briefly mention that your agency helps businesses like theirs automate their operations using AI (e.g. 24/7 AI call answering, automated lead follow-up, booking automation, admin reduction).
3. Ask a qualifying question about one of their specific pain points to spark a conversation.
4. If they are interested, offer to schedule a free 15-minute demo call with a human consultant.
5. If they are not interested, thank them politely and end the call graciously.

Tone: Friendly, confident, concise. Avoid being pushy or salesy. Speak naturally as if you are a real person.

Important:
- Always confirm you have reached {businessName} at the start.
- If a gatekeeper answers, try to reach the owner or manager.
- Do NOT leave a voicemail — simply end the call if you reach voicemail.
"""


def build_default_prompt(business_name: str, industry: str, services: str, pain_points: str) -> str:
    import os
    import zoneinfo
    from datetime import datetime
    tz_str = os.getenv("TIMEZONE", "Australia/Melbourne")
    try:
        tz = zoneinfo.ZoneInfo(tz_str)
    except Exception:
        tz = zoneinfo.ZoneInfo("Australia/Melbourne")
    now_local = datetime.now(tz)
    date_str = now_local.strftime("%A, %B %d, %Y")

    return DEFAULT_SYSTEM_PROMPT_TEMPLATE.format(
        date=date_str,
        businessName=business_name or "this business",
        industry=industry or "your industry",
        services=services or "various services",
        painPoints=pain_points or "operational inefficiencies and high workloads",
    )


# ---------------------------------------------------------------------------
# Vapi call outcome classifier
# ---------------------------------------------------------------------------

# endedReasons that mean the lead should go to "Call Back Later"
NO_ANSWER_REASONS = {
    "no-answer",
    "voicemail",
    "busy",
    "failed",
    "machine-detected-speech",
    "machine-detected-silence",
    "machine-ended-recording",
    "silence-timed-out",
    "pipeline-error",
    "call-start-error-twilio-failed-to-connect-call",
}

# endedReasons that mean the lead was actually reached (→ Contacted)
CONTACTED_REASONS = {
    "customer-hung-up",
    "assistant-ended-call",
    "assistant-completed-recording",
    "assistant-forwarded-call",
    "exceeded-max-duration",
}


def classify_call_outcome(ended_reason: str, duration_seconds: float = 0) -> str:
    """
    Returns 'no_answer' or 'contacted' based on the Vapi call's endedReason
    and call duration.

    A call is only 'contacted' if:
    - The endedReason suggests a human was reached, AND
    - The call lasted > 10 seconds.
    """
    reason = (ended_reason or "").lower().strip()
    if reason in NO_ANSWER_REASONS:
        return "no_answer"
    if reason in CONTACTED_REASONS and duration_seconds > 10:
        return "contacted"
    # Short calls (< 10s) even with a "completed" reason are treated as no-answer
    if duration_seconds <= 10:
        return "no_answer"
    return "no_answer"
