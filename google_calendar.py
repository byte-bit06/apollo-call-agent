import os
import json
import logging
from datetime import datetime, timedelta, time
import zoneinfo
from dotenv import load_dotenv

# Set up logging
logger = logging.getLogger("google_calendar")

# Load environment variables
load_dotenv()

# Configuration (Dynamically evaluated to pick up live dashboard updates)
def _get_calendar_id():
    return os.getenv("GOOGLE_CALENDAR_ID", "primary")

def _get_timezone_str():
    return os.getenv("TIMEZONE", "Australia/Melbourne")

def _get_tz():
    import zoneinfo
    try:
        return zoneinfo.ZoneInfo(_get_timezone_str())
    except Exception:
        return zoneinfo.ZoneInfo("Australia/Melbourne")

TZ = _get_tz()

# File path for mock calendar storage
MOCK_CALENDAR_FILE = os.path.join(os.path.dirname(__file__), "data", "mock_calendar.json")

def _get_google_service():
    """Initializes and returns the Google Calendar API service if credentials exist."""
    service_account_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    refresh_token = os.getenv("GOOGLE_REFRESH_TOKEN")

    # Method A: OAuth 2.0 (highest priority if refresh token is provided)
    if refresh_token and client_id and client_secret:
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            
            creds = Credentials(
                token=None,
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=client_id,
                client_secret=client_secret
            )
            service = build('calendar', 'v3', credentials=creds)
            logger.info("Successfully initialized Google Calendar API via OAuth 2.0.")
            return service
        except Exception as e:
            logger.error(f"Failed to initialize Google Calendar API via OAuth 2.0: {str(e)}")

    # Method B: Service Account
    if service_account_json:
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
            
            scopes = ['https://www.googleapis.com/auth/calendar']
            
            # Check if the env var contains the JSON string itself or a path
            if service_account_json.strip().startswith('{'):
                info = json.loads(service_account_json)
                creds = service_account.Credentials.from_service_account_info(info, scopes=scopes)
            else:
                # Assume it's a file path
                creds = service_account.Credentials.from_service_account_file(service_account_json, scopes=scopes)
                
            service = build('calendar', 'v3', credentials=creds)
            logger.info("Successfully initialized Google Calendar API via Service Account.")
            return service
        except Exception as e:
            logger.error(f"Failed to initialize Google Calendar API via Service Account: {str(e)}")

    logger.info("No live Google Calendar credentials found. Using mock calendar fallback.")
    return None

# --- MOCK CALENDAR IMPLEMENTATION ---
def _load_mock_events():
    """Loads mock calendar events from local JSON file."""
    if not os.path.exists(MOCK_CALENDAR_FILE):
        os.makedirs(os.path.dirname(MOCK_CALENDAR_FILE), exist_ok=True)
        # Create default mock events (some sample busy times)
        tomorrow = (datetime.now(TZ) + timedelta(days=1)).strftime("%Y-%m-%d")
        default_events = [
            {
                "id": "mock_1",
                "summary": "AI Automations Consultation - Sarah Jenkins",
                "start": f"{tomorrow}T10:00:00+10:00",
                "end": f"{tomorrow}T10:30:00+10:00",
                "description": "Phone: 0412345678, Topic: AI Chatbot Lead"
            },
            {
                "id": "mock_2",
                "summary": "Team Lunch meeting",
                "start": f"{tomorrow}T12:00:00+10:00",
                "end": f"{tomorrow}T13:00:00+10:00",
                "description": "Lunch meeting at cafeteria"
            }
        ]
        with open(MOCK_CALENDAR_FILE, "w") as f:
            json.dump(default_events, f, indent=4)
        return default_events
        
    try:
        with open(MOCK_CALENDAR_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to read mock calendar file: {str(e)}")
        return []

def _save_mock_event(event):
    """Saves a new event to the mock calendar file."""
    events = _load_mock_events()
    events.append(event)
    try:
        with open(MOCK_CALENDAR_FILE, "w") as f:
            json.dump(events, f, indent=4)
        return True
    except Exception as e:
        logger.error(f"Failed to save mock calendar event: {str(e)}")
        return False

# --- AVAILABILITY LOGIC ---
def get_free_slots(date_str: str) -> list:
    """
    Returns available 30-minute slots for a given date in Australia/Melbourne timezone.
    date_str format: YYYY-MM-DD
    """
    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        logger.error(f"Invalid date format: {date_str}. Expected YYYY-MM-DD.")
        return []

    # Exclude weekends (Saturday = 5, Sunday = 6)
    if target_date.weekday() in (5, 6):
        logger.info(f"Target date {date_str} is a weekend. No slots available.")
        return []

    TZ = _get_tz()
    GOOGLE_CALENDAR_ID = _get_calendar_id()
    TIMEZONE_STR = _get_timezone_str()

    # Check work hours: 9:00 AM to 5:00 PM (17:00) Melbourne Time
    start_work = datetime.combine(target_date, time(9, 0), tzinfo=TZ)
    end_work = datetime.combine(target_date, time(17, 0), tzinfo=TZ)
    
    # Generate all possible 30-minute slots in the window
    all_slots = []
    current_time = start_work
    while current_time < end_work:
        all_slots.append(current_time)
        current_time += timedelta(minutes=30)

    # Fetch busy times
    busy_intervals = []
    service = _get_google_service()
    
    if service:
        # Live Google Calendar implementation
        try:
            time_min = start_work.isoformat()
            time_max = end_work.isoformat()
            
            # Query FreeBusy
            body = {
                "timeMin": time_min,
                "timeMax": time_max,
                "timeZone": TIMEZONE_STR,
                "items": [{"id": GOOGLE_CALENDAR_ID}]
            }
            fb_response = service.freebusy().query(body=body).execute()
            calendars = fb_response.get("calendars", {})
            cal_data = calendars.get(GOOGLE_CALENDAR_ID, {})
            busy_list = cal_data.get("busy", [])
            
            for busy in busy_list:
                b_start = datetime.fromisoformat(busy["start"].replace("Z", "+00:00")).astimezone(TZ)
                b_end = datetime.fromisoformat(busy["end"].replace("Z", "+00:00")).astimezone(TZ)
                busy_intervals.append((b_start, b_end))
                
            logger.info(f"Retrieved {len(busy_intervals)} live busy slots for {date_str}.")
        except Exception as e:
            logger.error(f"Error querying live Google Calendar slots: {str(e)}. Falling back to mock calendar.")
            service = None # Trigger mock fallback
            
    if not service:
        # Mock Calendar implementation
        events = _load_mock_events()
        for event in events:
            ev_start = event.get("start")
            ev_end = event.get("end")
            if ev_start and ev_end:
                try:
                    s_dt = datetime.fromisoformat(ev_start).astimezone(TZ)
                    e_dt = datetime.fromisoformat(ev_end).astimezone(TZ)
                    # Check if the event is on the target date
                    if s_dt.date() == target_date or e_dt.date() == target_date:
                        busy_intervals.append((s_dt, e_dt))
                except Exception as e:
                    logger.error(f"Error parsing mock event dates: {str(e)}")

    # Filter out slots that overlap with busy times or are in the past
    now = datetime.now(TZ)
    free_slots = []
    
    for slot in all_slots:
        # Skip slots that are in the past
        if slot <= now:
            continue
            
        slot_end = slot + timedelta(minutes=30)
        is_busy = False
        
        # Check overlap with any busy interval
        for b_start, b_end in busy_intervals:
            # Overlap exists if slot_start < busy_end AND slot_end > busy_start
            if slot < b_end and slot_end > b_start:
                is_busy = True
                break
                
        if not is_busy:
            free_slots.append(slot.strftime("%H:%M"))

    logger.info(f"Found {len(free_slots)} available slots for date {date_str}.")
    return free_slots

# --- BOOKING LOGIC ---
def book_slot(name: str, email: str, phone: str, start_time_iso: str, course: str = "") -> dict:
    """
    Books a time slot on the Google Calendar.
    start_time_iso format: ISO 8601 string (e.g. 2026-07-20T10:00:00+10:00)
    """
    TZ = _get_tz()
    GOOGLE_CALENDAR_ID = _get_calendar_id()
    TIMEZONE_STR = _get_timezone_str()

    try:
        start_dt = datetime.fromisoformat(start_time_iso).astimezone(TZ)
        end_dt = start_dt + timedelta(minutes=30)
    except Exception as e:
        logger.error(f"Invalid booking ISO start time: {start_time_iso}. Error: {str(e)}")
        return {"success": False, "error": f"Invalid start time: {str(e)}"}

    summary = f"AI Automations Consultation - {name}"
    description = f"AI Automation Sales Consultation\n\nName: {name}\nEmail: {email}\nPhone: {phone}"
    if course:
        description += f"\nTopic of Interest: {course}"

    service = _get_google_service()
    
    if service:
        # Live Google Calendar booking
        try:
            event_body = {
                "summary": summary,
                "description": description,
                "start": {
                    "dateTime": start_dt.isoformat(),
                    "timeZone": TIMEZONE_STR
                },
                "end": {
                    "dateTime": end_dt.isoformat(),
                    "timeZone": TIMEZONE_STR
                },
                "reminders": {
                    "useDefault": True
                }
            }
            created_event = service.events().insert(calendarId=GOOGLE_CALENDAR_ID, body=event_body).execute()
            logger.info(f"Live Google Calendar slot booked! Event ID: {created_event.get('id')}")
            return {
                "success": True,
                "event_id": created_event.get("id"),
                "html_link": created_event.get("htmlLink"),
                "summary": summary,
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
                "mode": "live"
            }
        except Exception as e:
            logger.error(f"Failed to book live Google Calendar slot: {str(e)}. Falling back to mock booking.")
            service = None # Trigger mock fallback
            
    if not service:
        # Mock Calendar booking
        mock_id = f"mock_{int(datetime.now(TZ).timestamp())}"
        mock_event = {
            "id": mock_id,
            "summary": summary,
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "description": description,
            "attendee": email
        }
        saved = _save_mock_event(mock_event)
        if saved:
            logger.info(f"Mock calendar slot booked! Event ID: {mock_id}")
            return {
                "success": True,
                "event_id": mock_id,
                "html_link": "#mock-link",
                "summary": summary,
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
                "mode": "mock"
            }
        else:
            return {"success": False, "error": "Failed to save mock event."}
