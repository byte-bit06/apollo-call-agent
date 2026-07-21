import os
from dotenv import load_dotenv
from pathlib import Path

# Load env file
ENV_PATH = Path(__file__).parent.resolve() / ".env"
load_dotenv(str(ENV_PATH))

print("=== Supabase Connection Test ===")
print(f"URL: {os.getenv('SUPABASE_URL')}")
print(f"Key (masked): {os.getenv('SUPABASE_KEY', '')[:15]}...")

from supabase_db import db_load_leads, db_save_leads, db_load_calls, db_add_autopilot_log, db_load_autopilot_logs

try:
    print("\n1. Testing db_load_leads()...")
    leads = db_load_leads()
    print(f"Success! Loaded {len(leads)} lead(s).")
    
    print("\n2. Testing db_load_calls()...")
    calls = db_load_calls()
    print(f"Success! Loaded {len(calls)} call(s).")
    
    print("\n3. Testing db_add_autopilot_log()...")
    db_add_autopilot_log("Test log from python check script", "info")
    print("Success! Logged test message.")
    
    print("\n4. Testing db_load_autopilot_logs()...")
    logs = db_load_autopilot_logs()
    print(f"Success! Loaded {len(logs)} log(s).")
    
    print("\n=== All Tests Passed! ===")
except Exception as e:
    print(f"\nTest failed: {e}")
