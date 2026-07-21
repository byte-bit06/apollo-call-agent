import os
import requests
import json
import time
from dotenv import load_dotenv
from pathlib import Path
from urllib.parse import urlparse

# Load env variables
ENV_PATH = Path(__file__).parent.resolve() / ".env"
load_dotenv(str(ENV_PATH))

from outbound_crawler import gather_site_text, extract_business_profiles, normalise_au_phone, ddg_search
from supabase_db import db_load_leads, db_add_lead

def run():
    print("=== Scrape Real Estate & AI Automation Leads ===")
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY is not configured in .env")
        return
        
    # Load existing database leads
    existing_leads = db_load_leads()
    existing_domains = {l["domain"] for l in existing_leads}
    print(f"Loaded {len(existing_leads)} existing leads.")
    
    categories = {
        "Real Estate": "real estate agencies Melbourne",
        "IT Support": "IT support companies Melbourne",
        "Marketing": "marketing agencies Melbourne"
    }
    
    leads_added_count = 0
    
    for category_name, query in categories.items():
        print(f"\n--- Scraping Category: {category_name} ---")
        
        # Search DuckDuckGo (bypasses Google Search Grounding daily quota limits)
        candidate_urls = ddg_search(query, num_results=12)
        
        # Filter and clean domains
        unique_urls = []
        for u in candidate_urls:
            try:
                parsed = urlparse(u if u.startswith("http") else "https://" + u)
                domain = parsed.netloc.lower().lstrip("www.")
                if domain and domain not in existing_domains:
                    unique_urls.append(f"{parsed.scheme or 'https'}://{parsed.netloc}")
            except Exception:
                pass
                
        print(f"Processing {len(unique_urls)} unique domains for {category_name}...")
        
        category_leads_count = 0
        for url in unique_urls:
            if category_leads_count >= 10:
                print(f"Reached goal of 10 leads for {category_name}!")
                break
                
            domain = urlparse(url).netloc.lower().lstrip("www.")
            print(f"  Fetching site content: {domain}...")
            
            site_text = gather_site_text(url)
            if not site_text.strip():
                print("    Skipping (no content retrieved).")
                continue
                
            # Rate limit safety sleep
            print("    Waiting 4 seconds to comply with Gemini RPM rate limit...")
            time.sleep(4)
            
            profiles = extract_business_profiles(site_text, api_key=api_key)
            if not profiles:
                print("    Skipping (no phone number or valid profile).")
                continue
                
            # Save the lead on the fly
            import uuid
            from datetime import datetime, timezone
            for idx, profile in enumerate(profiles):
                unique_domain = domain if idx == 0 else f"{profile.get('businessName','').lower().replace(' ','')}.{domain}"
                if unique_domain in existing_domains:
                    continue
                    
                lead = {
                    "id": str(uuid.uuid4()),
                    "domain": unique_domain,
                    "url": url,
                    "businessName": profile.get("businessName", unique_domain),
                    "industry": category_name,
                    "services": profile.get("services", ""),
                    "painPoints": profile.get("painPoints", ""),
                    "phoneNumber": profile.get("phoneNumber", ""),
                    "status": "new",
                    "callCount": 0,
                    "lastCalledAt": None,
                    "lastCallOutcome": None,
                    "callHistory": [],
                    "addedAt": datetime.now(timezone.utc).isoformat(),
                }
                
                db_add_lead(lead)
                existing_domains.add(unique_domain)
                category_leads_count += 1
                leads_added_count += 1
                print(f"    [Saved] Lead Saved: {lead['businessName']} | {lead['phoneNumber']} | {lead['url']}")
                break
                
            time.sleep(1)
            
        print(f"Category {category_name} completed with {category_leads_count} leads.")
        
    print(f"\n=== Scrape Complete! Added {leads_added_count} leads to Supabase ===")

if __name__ == "__main__":
    run()
