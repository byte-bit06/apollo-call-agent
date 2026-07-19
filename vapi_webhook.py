import os
import json
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from urllib import request as urllib_request
from urllib.error import URLError, HTTPError
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, Response, BackgroundTasks, status, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vapi_webhook")

# Load environment variables
load_dotenv()

app = FastAPI(
    title="Vapi Webhook Email Sender",
    description="Webhook server for Vapi to send call reports to shaurya@y7mail.com",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DEFAULT_TO_EMAIL = os.getenv("EMAIL_TO", "shxuryatech@gmail.com")

# Easiest way is Resend (https://resend.com) - get a free API key, no card needed.
RESEND_API_KEY = os.getenv("RESEND_API_KEY") 
# Alternative standard SMTP
SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = os.getenv("SMTP_PORT", "587")
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
EMAIL_FROM = os.getenv("EMAIL_FROM", "onboarding@resend.dev") # Default Resend sending email

# Microsoft Graph API Method (Outlook/Office 365)
MICROSOFT_GRAPH_TOKEN = os.getenv("MICROSOFT_GRAPH_TOKEN")

def format_duration(seconds: float) -> str:
    if not seconds:
        return "0s"
    try:
        seconds = float(seconds)
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        if mins > 0:
            return f"{mins}m {secs}s"
        return f"{secs}s"
    except Exception:
        return f"{seconds}s"

def generate_html_email(
    event_type: str, 
    call: dict, 
    summary: str, 
    transcript: str, 
    contact_name: str = None,
    email_collected: str = None,
    company_name: str = None,
    demo_requested: str = None,
    follow_up_timeframe: str = None,
    primary_pain_points = None,
    success_evaluation: str = None
) -> str:
    """Generates a beautiful, responsive HTML email for the call report."""
    call_id = call.get("id", "N/A")
    status_val = call.get("status", "completed")
    ended_reason = call.get("endedReason", "N/A")
    
    # Format phone number
    customer = call.get("customer", {})
    phone = customer.get("number", "Unknown Caller")
    
    # Cost and duration
    cost = call.get("cost", 0.0)
    duration_sec = call.get("duration", 0.0)
    duration_str = format_duration(duration_sec)
    
    # Recording URL
    recording_url = call.get("recordingUrl") or call.get("artifact", {}).get("recordingUrl", "")
    
    # Stylized statuses
    status_color = "#10b981" if status_val in ["ended", "completed"] else "#f59e0b"
    if ended_reason in ["customer-hung-up", "assistant-completed-recording"]:
        reason_color = "#3b82f6"
    elif ended_reason in ["error", "pipeline-error"]:
        reason_color = "#ef4444"
    else:
        reason_color = "#6b7280"

    # Success score section
    success_section = ""
    if success_evaluation and success_evaluation != "N/A":
        success_section = f"""
        <div style="background-color: #ecfdf5; border: 1px solid #a7f3d0; padding: 14px 18px; border-radius: 8px; margin-bottom: 24px; text-align: left;">
            <span style="color: #065f46; font-size: 11px; font-weight: 700; text-transform: uppercase; display: inline-block; margin-bottom: 4px; letter-spacing: 0.05em;">Success Evaluation</span>
            <div style="font-size: 22px; font-weight: 800; color: #047857;">{success_evaluation} <span style="font-size: 14px; font-weight: 500; color: #065f46;">/ 10</span></div>
        </div>"""

    # Lead Information Section
    lead_details_html = ""
    if contact_name or email_collected or company_name:
        company_line = f"<p style='margin: 6px 0 0 0; color: #475569; font-size: 14px;'>Company: <strong>{company_name}</strong></p>" if company_name else ""
        lead_details_html = f"""
        <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; padding: 18px; border-radius: 8px; margin-bottom: 24px;">
            <h3 style="margin-top: 0; color: #0f172a; font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; border-bottom: 1px solid #e2e8f0; padding-bottom: 8px; margin-bottom: 12px;">Contact Information</h3>
            <p style="margin: 0; color: #1e293b; font-size: 16px; font-weight: 700;">{contact_name or 'Anonymous Customer'}</p>
            <p style="margin: 8px 0 0 0; color: #334155; font-size: 14px;">Email: <a href="mailto:{email_collected}" style="color: #4f46e5; text-decoration: none; font-weight: 600;">{email_collected or 'Not collected'}</a></p>
            {company_line}
        </div>"""

    # Next Steps / Booking Section
    booking_status = "No"
    booking_bg = "#f3f4f6"
    booking_color = "#4b5563"
    
    if demo_requested is not None:
        demo_str = str(demo_requested).lower()
        if demo_str in ("true", "yes", "1", "booked", "requested"):
            booking_status = "Yes (Booked/Requested)"
            booking_bg = "#dcfce7"
            booking_color = "#15803d"
        elif demo_str in ("false", "no", "0"):
            booking_status = "No"
            booking_bg = "#fee2e2"
            booking_color = "#b91c1c"
        else:
            booking_status = str(demo_requested)
            
    booking_html = f"""
    <div style="background-color: #ffffff; border: 1px solid #e2e8f0; padding: 18px; border-radius: 8px; margin-bottom: 24px;">
        <h3 style="margin-top: 0; color: #0f172a; font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; border-bottom: 1px solid #e2e8f0; padding-bottom: 8px; margin-bottom: 12px;">Next Steps / Booking</h3>
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0">
            <tr>
                <td style="padding: 8px 0; font-size: 14px; color: #475569;">Demo Requested / Booked:</td>
                <td style="padding: 8px 0; text-align: right;">
                    <span style="background-color: {booking_bg}; color: {booking_color}; padding: 3px 10px; border-radius: 12px; font-size: 12px; font-weight: 700; display: inline-block;">
                        {booking_status}
                    </span>
                </td>
            </tr>
            <tr>
                <td style="padding: 8px 0; font-size: 14px; color: #475569;">Preferred Follow-up Time:</td>
                <td style="padding: 8px 0; font-size: 14px; color: #0f172a; font-weight: 600; text-align: right;">
                    {follow_up_timeframe or 'Not scheduled'}
                </td>
            </tr>
        </table>
    </div>"""

    # Primary Pain Points Section
    pain_points_html = ""
    if primary_pain_points:
        if isinstance(primary_pain_points, list):
            items = "".join(f"<li style='margin-bottom: 6px;'>{item}</li>" for item in primary_pain_points if item)
            if items:
                pain_points_html = f"<ul style='margin: 0; padding-left: 20px; color: #334155; font-size: 14px;'>{items}</ul>"
        elif isinstance(primary_pain_points, str):
            pain_points_html = f"<p style='margin: 0; color: #334155; font-size: 14px; line-height: 1.5;'>{primary_pain_points}</p>"
            
    if not pain_points_html:
        pain_points_html = "<p style='margin: 0; color: #94a3b8; font-style: italic; font-size: 14px;'>None identified during this call.</p>"

    pain_points_section = f"""
    <div style="background-color: #fffbeb; border: 1px solid #fef3c7; padding: 18px; border-radius: 8px; margin-bottom: 24px;">
        <h3 style="margin-top: 0; color: #78350f; font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; border-bottom: 1px solid #fde68a; padding-bottom: 8px; margin-bottom: 12px;">Primary Pain Points & Issues</h3>
        {pain_points_html}
    </div>"""

    # Format transcript lines
    transcript_html = ""
    if transcript:
        lines = transcript.strip().split("\n")
        for line in lines:
            if ":" in line:
                speaker, text = line.split(":", 1)
                speaker = speaker.strip()
                text = text.strip()
                if speaker.lower() in ["assistant", "agent", "bot"]:
                    transcript_html += f"""
                    <div style="margin-bottom: 12px; text-align: left;">
                        <span style="display: inline-block; background-color: #f3f4f6; color: #1f2937; padding: 8px 12px; border-radius: 12px 12px 12px 0px; font-size: 14px; max-width: 85%;">
                            <strong style="color: #4f46e5; display: block; font-size: 11px; margin-bottom: 2px; text-transform: uppercase;">Assistant</strong>
                            {text}
                        </span>
                    </div>"""
                else:
                    transcript_html += f"""
                    <div style="margin-bottom: 12px; text-align: right;">
                        <span style="display: inline-block; background-color: #e0e7ff; color: #1e3a8a; padding: 8px 12px; border-radius: 12px 12px 0px 12px; font-size: 14px; max-width: 85%; text-align: left;">
                            <strong style="color: #2563eb; display: block; font-size: 11px; margin-bottom: 2px; text-transform: uppercase;">{speaker}</strong>
                            {text}
                        </span>
                    </div>"""
            else:
                transcript_html += f'<p style="font-size: 14px; color: #374151; line-height: 1.5; margin: 4px 0;">{line}</p>'
    else:
        transcript_html = f"""
        <div style="text-align: center; padding: 20px 0;">
            <p style="color: #9ca3af; font-style: italic; font-size: 14px; margin: 0 0 8px 0;">No transcript available for this call yet.</p>
            <p style="color: #6b7280; font-size: 13px; margin: 0;">Report is still processing for call {call_id}</p>
        </div>"""

    recording_section = ""
    if recording_url:
        recording_section = f"""
        <div style="margin-top: 20px; text-align: center;">
            <a href="{recording_url}" style="display: inline-block; background-color: #4f46e5; color: #ffffff; padding: 10px 20px; border-radius: 6px; text-decoration: none; font-weight: bold; font-size: 14px; box-shadow: 0 4px 6px rgba(79, 70, 229, 0.15);">
                Listen to Recording
            </a>
        </div>"""

    summary_section = ""
    if summary:
        summary_section = f"""
        <div style="margin-bottom: 24px; background-color: #fcfcfd; border-left: 4px solid #4f46e5; padding: 16px; border-radius: 0 8px 8px 0; box-shadow: inset 0 1px 2px rgba(0,0,0,0.02);">
            <h3 style="margin-top: 0; color: #1e1b4b; font-size: 16px; margin-bottom: 8px;">Call Summary</h3>
            <p style="color: #4b5563; font-size: 14px; line-height: 1.6; margin: 0;">{summary}</p>
        </div>"""
    else:
        summary_section = f"""
        <div style="margin-bottom: 24px; background-color: #fffbeb; border-left: 4px solid #f59e0b; padding: 16px; border-radius: 0 8px 8px 0;">
            <h3 style="margin-top: 0; color: #78350f; font-size: 16px; margin-bottom: 8px;">Processing Status</h3>
            <p style="color: #b45309; font-size: 14px; line-height: 1.6; margin: 0; font-weight: 500;">Report is still processing for call {call_id}. Transcript and summary will be populated shortly.</p>
        </div>"""

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Vapi Call Report</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f3f4f6; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; -webkit-font-smoothing: antialiased;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color: #f3f4f6; padding: 20px 0;">
        <tr>
            <td align="center">
                <table role="presentation" width="600" cellspacing="0" cellpadding="0" style="background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05); border: 1px solid #e5e7eb;">
                    <!-- Header -->
                    <tr>
                        <td style="background: linear-gradient(135deg, #1e1b4b 0%, #312e81 100%); padding: 30px 40px; text-align: center;">
                            <h1 style="color: #ffffff; font-size: 24px; font-weight: 700; margin: 0; letter-spacing: -0.025em;">AI Automations Call Report</h1>
                            <p style="color: #c7d2fe; font-size: 14px; margin: 8px 0 0 0;">Event: {event_type}</p>
                        </td>
                    </tr>
                    
                    <!-- Content Body -->
                    <tr>
                        <td style="padding: 30px 40px;">
                            <!-- Metrics Grid -->
                            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin-bottom: 24px;">
                                <tr>
                                    <td width="50%" style="padding-bottom: 15px; padding-right: 10px;">
                                        <span style="color: #6b7280; font-size: 11px; text-transform: uppercase; font-weight: 600; display: block; margin-bottom: 2px;">Customer Number</span>
                                        <strong style="color: #111827; font-size: 15px;">{phone}</strong>
                                    </td>
                                    <td width="50%" style="padding-bottom: 15px; padding-left: 10px;">
                                        <span style="color: #6b7280; font-size: 11px; text-transform: uppercase; font-weight: 600; display: block; margin-bottom: 2px;">Duration</span>
                                        <strong style="color: #111827; font-size: 15px;">{duration_str}</strong>
                                    </td>
                                </tr>
                                <tr>
                                    <td width="50%" style="padding-bottom: 15px; padding-right: 10px;">
                                        <span style="color: #6b7280; font-size: 11px; text-transform: uppercase; font-weight: 600; display: block; margin-bottom: 2px;">End Reason</span>
                                        <span style="display: inline-block; background-color: {reason_color}15; color: {reason_color}; font-size: 12px; font-weight: 600; padding: 2px 8px; border-radius: 4px;">{ended_reason}</span>
                                    </td>
                                    <td width="50%" style="padding-bottom: 15px; padding-left: 10px;">
                                        <span style="color: #6b7280; font-size: 11px; text-transform: uppercase; font-weight: 600; display: block; margin-bottom: 2px;">Cost</span>
                                        <strong style="color: #111827; font-size: 15px;">${cost:.4f}</strong>
                                    </td>
                                </tr>
                            </table>
                            
                            {success_section}
                            
                            {lead_details_html}
                            
                            {booking_html}
                            
                            {pain_points_section}
                            
                            {summary_section}
                            
                            <!-- Transcript Area -->
                            <div style="border-top: 1px solid #e5e7eb; padding-top: 24px;">
                                <h3 style="margin-top: 0; color: #111827; font-size: 16px; margin-bottom: 16px;">Call Transcript</h3>
                                <div style="background-color: #fafafa; border: 1px solid #f3f4f6; border-radius: 8px; padding: 16px; max-height: 400px; overflow-y: auto;">
                                    {transcript_html}
                                </div>
                            </div>
                            
                            {recording_section}
                            
                            <!-- Footer Metadata -->
                            <div style="border-top: 1px solid #e5e7eb; margin-top: 30px; padding-top: 15px; text-align: center;">
                                <span style="color: #9ca3af; font-size: 11px; display: block;">Call ID: {call_id}</span>
                            </div>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>"""

def send_via_outlook_rest(html_content: str, subject: str) -> bool:
    """Sends email via Outlook REST API v2.0 using legacy/opaque tokens (like EwBY)."""
    logger.info("Attempting to send email via Outlook REST API v2.0...")
    url = "https://outlook.office.com/api/v2.0/me/sendmail"
    headers = {
        "Authorization": f"Bearer {MICROSOFT_GRAPH_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "Message": {
            "Subject": subject,
            "Body": {
                "ContentType": "HTML",
                "Content": html_content
            },
            "ToRecipients": [
                {
                    "EmailAddress": {
                        "Address": DEFAULT_TO_EMAIL
                    }
                }
            ]
        },
        "SaveToSentItems": "true"
    }
    try:
        req = urllib_request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST"
        )
        with urllib_request.urlopen(req) as response:
            logger.info(f"Email sent successfully via Outlook REST API. Status: {response.status}")
            return True
    except HTTPError as e:
        logger.error(f"HTTP Error sending via Outlook REST: {e.code} - {e.reason}")
        try:
            logger.error(f"Details: {e.read().decode('utf-8')}")
        except Exception:
            pass
        return False
    except Exception as e:
        logger.error(f"Failed to send email via Outlook REST API: {str(e)}")
        return False

def send_via_ms_graph(html_content: str, subject: str) -> bool:
    """Sends email via modern Microsoft Graph API (expects JWT token with dots)."""
    logger.info("Attempting to send email via Microsoft Graph API...")
    url = "https://graph.microsoft.com/v1.0/me/sendMail"
    headers = {
        "Authorization": f"Bearer {MICROSOFT_GRAPH_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "message": {
            "subject": subject,
            "body": {
                "contentType": "HTML",
                "content": html_content
            },
            "toRecipients": [
                {
                    "emailAddress": {
                        "address": DEFAULT_TO_EMAIL
                    }
                }
            ]
        }
    }
    try:
        req = urllib_request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST"
        )
        with urllib_request.urlopen(req) as response:
            logger.info(f"Email sent successfully via Microsoft Graph API. Status: {response.status}")
            return True
    except HTTPError as e:
        logger.error(f"HTTP Error sending via MS Graph: {e.code} - {e.reason}")
        try:
            logger.error(f"Details: {e.read().decode('utf-8')}")
        except Exception:
            pass
        return False
    except Exception as e:
        logger.error(f"Failed to send email via MS Graph: {str(e)}")
        return False

def send_via_microsoft(html_content: str, subject: str) -> bool:
    """Dispatches sending to the correct Microsoft endpoint based on token type."""
    if not MICROSOFT_GRAPH_TOKEN:
        return False
        
    if MICROSOFT_GRAPH_TOKEN.count(".") == 0:
        logger.info("Opaque token detected (0 dots). Routing directly to Outlook REST API...")
        return send_via_outlook_rest(html_content, subject)
    else:
        logger.info("JWT token detected (contains dots). Routing to Microsoft Graph API...")
        if send_via_ms_graph(html_content, subject):
            return True
        logger.info("Graph API failed. Attempting Outlook REST API fallback...")
        return send_via_outlook_rest(html_content, subject)

def send_via_resend(html_content: str, subject: str) -> bool:
    """Sends email via Resend API using standard urllib."""
    url = "https://api.resend.com/emails"
    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type": "application/json",
        "User-Agent": "resend-python/2.0.0"
    }
    payload = {
        "from": EMAIL_FROM,
        "to": [DEFAULT_TO_EMAIL],
        "subject": subject,
        "html": html_content
    }
    
    try:
        req = urllib_request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST"
        )
        with urllib_request.urlopen(req) as response:
            res_body = response.read().decode("utf-8")
            logger.info(f"Resend success: {res_body}")
            return True
    except URLError as e:
        logger.error(f"Failed to send email via Resend: {str(e)}")
        # Check if there is details in HTTPError
        if hasattr(e, "read"):
            try:
                error_details = e.read().decode("utf-8")
                logger.error(f"Resend Error details: {error_details}")
            except Exception:
                pass
        return False

def send_via_smtp(html_content: str, subject: str) -> bool:
    """Sends email via standard SMTP (smtplib)."""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = EMAIL_FROM
        msg["To"] = DEFAULT_TO_EMAIL
        
        part_html = MIMEText(html_content, "html")
        msg.attach(part_html)
        
        # Connect to SMTP server
        server = smtplib.SMTP(SMTP_HOST, int(SMTP_PORT))
        server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.sendmail(EMAIL_FROM, [DEFAULT_TO_EMAIL], msg.as_string())
        server.quit()
        logger.info("SMTP email sent successfully.")
        return True
    except Exception as e:
        logger.error(f"Failed to send email via SMTP: {str(e)}")
        return False

def process_webhook_payload(payload: dict):
    """Processes Vapi webhook payload and dispatches the email."""
    # Log raw webhook payload for debugging/inspection
    logger.info(f"Received Vapi Webhook Payload: {json.dumps(payload)}")
    
    # Vapi sends { "message": { "type": "...", "call": {...} } } or directly { "type": "...", "call": {...} }
    message = payload.get("message", {})
    if not message or not isinstance(message, dict):
        message = payload
        
    event_type = message.get("type", "unknown-event")
    
    # We only care about end-of-call-report to prevent blank intermediate status emails
    if event_type != "end-of-call-report":
        logger.info(f"Ignoring non-terminal event '{event_type}'. We only process 'end-of-call-report'.")
        return
        
    call = message.get("call", {})
    if not isinstance(call, dict):
        call = {}
        
    # Extract artifact
    artifact = message.get("artifact") or call.get("artifact") or {}
    if not isinstance(artifact, dict):
        artifact = {}
        
    # Extract transcript fields
    transcript = message.get("transcript") or call.get("transcript") or artifact.get("transcript", "")
    if not transcript and "messages" in message:
        # Fallback: construct transcript from messages list if transcript field is empty
        msgs = message.get("messages", [])
        if msgs and isinstance(msgs, list):
            transcript = "\n".join([f"{m.get('role', 'unknown').capitalize()}: {m.get('message', '')}" for m in msgs if m.get('message')])

    # Extract analysis fields
    analysis = message.get("analysis") or call.get("analysis") or {}
    if not isinstance(analysis, dict):
        analysis = {}
        
    summary = analysis.get("summary") or message.get("summary") or call.get("summary", "")
    success_evaluation = analysis.get("successEvaluation") or analysis.get("success_evaluation") or message.get("successEvaluation") or "N/A"
    
    # Extract structured data fields
    structured_data = analysis.get("structuredData") or analysis.get("structured_data") or message.get("structuredData") or {}
    if not isinstance(structured_data, dict):
        structured_data = {}
        
    email_collected = structured_data.get("email_collected") or structured_data.get("email")
    demo_requested = structured_data.get("demo_requested") or structured_data.get("demo_booked")
    follow_up_timeframe = structured_data.get("follow_up_timeframe") or structured_data.get("time_to_book")
    primary_pain_points = structured_data.get("primary_pain_points") or structured_data.get("issues")
    contact_name = structured_data.get("contact_name") or structured_data.get("name")
    company_name = structured_data.get("company_name") or structured_data.get("company")
    
    # Extract customer details
    customer = call.get("customer") or message.get("customer") or {}
    if not isinstance(customer, dict):
        customer = {}
    phone = customer.get("number") or call.get("customer", {}).get("number") or "Unknown"
    
    subject = f"AI Automations Call Report: {phone} - {event_type.replace('-', ' ').title()}"
    html_content = generate_html_email(
        event_type=event_type,
        call=call,
        summary=summary,
        transcript=transcript,
        contact_name=contact_name,
        email_collected=email_collected,
        company_name=company_name,
        demo_requested=demo_requested,
        follow_up_timeframe=follow_up_timeframe,
        primary_pain_points=primary_pain_points,
        success_evaluation=str(success_evaluation) if success_evaluation is not None else "N/A"
    )
    
    # Select mechanism with cascading fallback
    sent = False
    if MICROSOFT_GRAPH_TOKEN:
        sent = send_via_microsoft(html_content, subject)
    if not sent and RESEND_API_KEY:
        sent = send_via_resend(html_content, subject)
    if not sent and SMTP_HOST and SMTP_USERNAME and SMTP_PASSWORD:
        sent = send_via_smtp(html_content, subject)
        
    if not sent:
        logger.warning(
            "\n" + "="*80 + "\n"
            "⚠️  NO EMAIL SENDER CONFIGURED / FAILED TO SEND!\n"
            "To send emails, please configure either RESEND_API_KEY or SMTP variables in your .env file.\n"
            f"Subject: {subject}\n"
            f"Recipient: {DEFAULT_TO_EMAIL}\n"
            f"Call ID: {call.get('id')}\n"
            "Outputting generated HTML content for verification:\n"
            + "="*80 + "\n"
            + html_content + "\n"
            + "="*80
        )

def parse_date_time_to_iso(date_str: str, time_str: str) -> str:
    """Combines YYYY-MM-DD and a time string (e.g. 10:30, 2:30 PM) into an ISO datetime string in Melbourne timezone."""
    time_str = time_str.strip().upper()
    is_pm = "PM" in time_str
    is_am = "AM" in time_str
    
    clean_time = time_str.replace("AM", "").replace("PM", "").strip()
    parts = clean_time.split(":")
    
    hour = int(parts[0])
    minute = int(parts[1]) if len(parts) > 1 else 0
    
    if is_pm and hour < 12:
        hour += 12
    elif is_am and hour == 12:
        hour = 0
        
    from google_calendar import TZ
    target_dt = datetime.strptime(date_str, "%Y-%m-%d")
    target_dt = target_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    localized_dt = target_dt.replace(tzinfo=TZ)
    return localized_dt.isoformat()

def process_vapi_tool_calls(payload: dict) -> dict:
    """Processes Vapi tool calls and returns the response structure Vapi expects."""
    message = payload.get("message", {}) if isinstance(payload, dict) else {}
    tool_calls = message.get("toolCalls") or message.get("toolCallList") or []
    results = []
    
    for tc in tool_calls:
        tc_id = tc.get("id")
        func_name = tc.get("function", {}).get("name")
        args = tc.get("function", {}).get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        if not isinstance(args, dict):
            args = {}
            
        if func_name in ("send_email", "send_mail", "send_email_tool"):
            subject = args.get("subject") or "Information from Hammond Institute"
            body = args.get("body") or args.get("content") or args.get("content_html") or args.get("message") or args.get("query")
            
            if not body:
                results.append({"toolCallId": tc_id, "result": "Error: Missing email body/content."})
                continue
                
            if not body.strip().startswith("<"):
                html_body = "".join(f"<p>{line}</p>" for line in body.split("\n") if line.strip())
            else:
                html_body = body
                
            # Dispatch send with cascading fallback
            sent = False
            if MICROSOFT_GRAPH_TOKEN:
                sent = send_via_microsoft(html_body, subject)
            if not sent and RESEND_API_KEY:
                sent = send_via_resend(html_body, subject)
            if not sent and SMTP_HOST and SMTP_USERNAME and SMTP_PASSWORD:
                sent = send_via_smtp(html_body, subject)
                
            if sent:
                results.append({"toolCallId": tc_id, "result": "Email sent successfully."})
            else:
                results.append({"toolCallId": tc_id, "result": "Failed to send email. Check webhook server logs."})
                
        elif func_name in ("check_calendar_availability", "check_availability", "get_slots", "list_available_slots"):
            from google_calendar import get_free_slots, TZ
            date_str = args.get("date")
            if not date_str:
                # Default to tomorrow Melbourne time
                tomorrow = datetime.now(TZ) + timedelta(days=1)
                # If tomorrow is Saturday, shift to Monday (weekday = 0)
                if tomorrow.weekday() == 5: # Saturday
                    tomorrow += timedelta(days=2)
                elif tomorrow.weekday() == 6: # Sunday
                    tomorrow += timedelta(days=1)
                date_str = tomorrow.strftime("%Y-%m-%d")
                
            try:
                slots = get_free_slots(date_str)
                if slots:
                    slot_list = ", ".join(slots)
                    result_msg = f"Available time slots for {date_str} (Melbourne Time) are: {slot_list}. Please ask the client which of these times works best for them."
                else:
                    result_msg = f"There are no available slots for {date_str}. Please suggest a different date (Monday to Friday, between 10:00 AM and 5:00 PM)."
                results.append({"toolCallId": tc_id, "result": result_msg})
            except Exception as e:
                results.append({"toolCallId": tc_id, "result": f"Error checking availability: {str(e)}. Please try a date format like YYYY-MM-DD (e.g. 2026-07-20)."})
                
        elif func_name in ("book_calendar_slot", "book_appointment", "create_booking", "schedule_appointment"):
            from google_calendar import book_slot
            name = args.get("name")
            email = args.get("email")
            phone = args.get("phone")
            date_str = args.get("date")
            time_slot = args.get("time_slot") or args.get("time")
            slot_time = args.get("slot_time") or args.get("slotTime")
            course = args.get("course") or args.get("course_name") or ""
            
            if not (name and email and phone):
                results.append({"toolCallId": tc_id, "result": "Error: Missing required details. To book a slot, I need the client's name, email, and phone number."})
                continue
                
            if not slot_time and date_str and time_slot:
                try:
                    slot_time = parse_date_time_to_iso(date_str, time_slot)
                except Exception as e:
                    results.append({"toolCallId": tc_id, "result": f"Error parsing date/time: {str(e)}. Please provide a date like YYYY-MM-DD and a time like 10:30 AM."})
                    continue
                    
            if not slot_time:
                results.append({"toolCallId": tc_id, "result": "Error: Missing booking time slot. Please provide both a date and a time to book."})
                continue
                
            try:
                res = book_slot(name, email, phone, slot_time, course)
                if res.get("success"):
                    mode_str = "Google Calendar" if res.get("mode") == "live" else "Mock Calendar"
                    results.append({
                        "toolCallId": tc_id, 
                        "result": f"Booking successful! Scheduled appointment on {mode_str} for {name} at {res.get('start')}."
                    })
                else:
                    results.append({"toolCallId": tc_id, "result": f"Failed to book slot: {res.get('error')}"})
            except Exception as e:
                results.append({"toolCallId": tc_id, "result": f"Error booking slot: {str(e)}"})
                
        else:
            results.append({"toolCallId": tc_id, "result": f"Unknown function: {func_name}"})
            
    return {"results": results}

@app.post("/webhook")
async def vapi_webhook(request: Request, background_tasks: BackgroundTasks):
    """Vapi Webhook Endpoint. Handles synchronous tool calls and asynchronous call reports."""
    try:
        payload = await request.json()
    except Exception as e:
        logger.error(f"Invalid JSON payload: {str(e)}")
        return Response(content=json.dumps({"error": "Invalid JSON"}), status_code=status.HTTP_400_BAD_REQUEST, media_type="application/json")
        
    message = payload.get("message", {}) if isinstance(payload, dict) else {}
    event_type = message.get("type")
    
    # Check if this is a Vapi tool call
    if event_type == "tool-calls" or "toolCalls" in message or "toolCallList" in message:
        return process_vapi_tool_calls(payload)
        
    # Queue standard events/reports processing to background task to return fast 200 OK
    background_tasks.add_task(process_webhook_payload, payload)
    return {"status": "accepted", "message": "Call report processing in progress"}

@app.get("/api/calendar/slots")
def get_calendar_slots_api(date: str):
    """Retrieve available slots on a given date (format: YYYY-MM-DD)."""
    from google_calendar import get_free_slots
    try:
        slots = get_free_slots(date)
        return {"date": date, "slots": slots}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/calendar/book")
def book_calendar_slot_api(payload: dict):
    """Book a time slot."""
    from google_calendar import book_slot
    name = payload.get("name")
    email = payload.get("email")
    phone = payload.get("phone")
    date_str = payload.get("date")
    time_slot = payload.get("time_slot") or payload.get("time")
    slot_time = payload.get("slot_time") or payload.get("slotTime")
    course = payload.get("course", "")
    
    if not (name and email and phone):
        raise HTTPException(status_code=400, detail="Missing required booking details (name, email, phone).")
        
    if not slot_time and date_str and time_slot:
        try:
            slot_time = parse_date_time_to_iso(date_str, time_slot)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to parse date/time: {str(e)}")
            
    if not slot_time:
        raise HTTPException(status_code=400, detail="Missing booking time slot (slot_time or date + time required).")
        
    try:
        res = book_slot(name, email, phone, slot_time, course)
        if res.get("success"):
            return res
        else:
            raise HTTPException(status_code=500, detail=res.get("error"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
def gateway_status():
    """Returns status of the webhook gateway."""
    return {
        "status": "online",
        "service": "AI Automations Voice Agent Webhook Gateway",
        "endpoints": {
            "root_router": "/",
            "email_webhook": "/webhook",
            "calendar_slots": "/api/calendar/slots",
            "calendar_book": "/api/calendar/book"
        }
    }

@app.post("/")
async def root_webhook_router(request: Request, background_tasks: BackgroundTasks):
    """Smart router on the root POST path to support Vapi webhooks pointing directly to the base URL."""
    try:
        payload = await request.json()
    except Exception as e:
        logger.error(f"Invalid JSON payload on root: {str(e)}")
        return Response(content=json.dumps({"error": "Invalid JSON"}), status_code=status.HTTP_400_BAD_REQUEST, media_type="application/json")
        
    message = payload.get("message", {}) if isinstance(payload, dict) else {}
    event_type = message.get("type")
    
    # 1. Check if this is a Vapi tool call
    if event_type == "tool-calls" or "toolCalls" in message or "toolCallList" in message:
        tool_calls = message.get("toolCalls") or message.get("toolCallList") or []
        
        if tool_calls:
            first_tc = tool_calls[0]
            func_name = first_tc.get("function", {}).get("name", "")
            
            # If it's an email or calendar tool call, handle it synchronously here
            if func_name in (
                "send_email", "send_mail", "send_email_tool",
                "check_calendar_availability", "check_availability", "get_slots", "list_available_slots",
                "book_calendar_slot", "book_appointment", "create_booking", "schedule_appointment"
            ):
                return process_vapi_tool_calls(payload)
            
        return {"results": []}
        
    # 2. Treat as a standard Vapi event/report on port 8000
    background_tasks.add_task(process_webhook_payload, payload)
    return {"status": "accepted", "message": "Call report processing in progress"}

if __name__ == "__main__":
    import uvicorn
    # Allow specifying PORT from environment
    port = int(os.getenv("PORT", "8000"))
    logger.info(f"Starting server on port {port}...")
    uvicorn.run("vapi_webhook:app", host="0.0.0.0", port=port, reload=True)
