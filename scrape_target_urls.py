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

from outbound_crawler import gather_site_text, extract_business_profiles
from supabase_db import db_load_leads, db_add_lead

URLS = [
    # --- Real Estate Agencies Melbourne ---
    "https://www.jelliscraig.com.au",
    "https://www.marshallwhite.com.au",
    "https://www.barryplant.com.au",
    "https://www.nelsonalexander.com.au",
    "https://www.woodards.com.au",
    "https://www.rtedgar.com.au",
    "https://www.obrienrealestate.com.au",
    "https://www.fletchers.net.au",
    "https://www.greghocking.com.au",
    "https://www.hodges.com.au",
    "https://www.hockingstuart.com.au",
    "https://www.littlerealestate.com.au",
    
    # --- IT Support / Tech (Want AI Automations) ---
    "https://www.intelife.com.au",
    "https://www.itsupportmelbourne.com.au",
    "https://www.melbourneitsupport.com.au",
    "https://www.systemace.com.au",
    
    # --- Marketing Agencies Melbourne (Want AI Automations) ---
    "https://www.melbournemarketing.com.au",
    "https://www.clickmatix.com.au",
    "https://www.socialgarden.com.au",
    "https://www.impulsives.com.au"
]

def run():
    print("=== Scrape Target Real Estate & B2B Leads ===")
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY is not configured in .env")
        return
        
    # Load existing database leads
    existing_leads = db_load_leads()
    existing_domains = {l["domain"] for l in existing_leads}
    print(f"Loaded {len(existing_leads)} existing leads.")
    
    leads_added_count = 0
    
    # Filter out already crawled domains
    target_urls = []
    for u in URLS:
        try:
            parsed = urlparse(u)
            domain = parsed.netloc.lower().lstrip("www.")
            if domain not in existing_domains:
                target_urls.append(u)
        except Exception:
            pass
            
    print(f"Processing {len(target_urls)} new domains...")
    
    for url in target_urls:
        domain = urlparse(url).netloc.lower().lstrip("www.")
        print(f"\nProcessing: {domain}...")
        
        # Crawl site content
        site_text = gather_site_text(url)
        if not site_text.strip():
            print("  Skipping (no content retrieved).")
            continue
            
        # Rate limit safety sleep
        print("  Waiting 4 seconds to comply with Gemini RPM rate limit...")
        time.sleep(4)
        
        profiles = extract_business_profiles(site_text, api_key=api_key)
        if not profiles:
            print("  Skipping (no phone number or valid profile).")
            continue
            
        # Save lead on the fly
        import uuid
        from datetime import datetime, timezone
        for idx, profile in enumerate(profiles):
            unique_domain = domain if idx == 0 else f"{profile.get('businessName','').lower().replace(' ','')}.{domain}"
            if unique_domain in existing_domains:
                continue
                
            # Determine industry based on keyword or default
            services_lower = profile.get("services", "").lower()
            industry_val = profile.get("industry", "Real Estate")
            if "marketing" in services_lower or "seo" in services_lower or "digital agency" in services_lower:
                industry_val = "Marketing"
            elif "it support" in services_lower or "managed it" in services_lower or "tech" in services_lower:
                industry_val = "IT Support"
            elif "real estate" in services_lower or "property" in services_lower or "rent" in services_lower:
                industry_val = "Real Estate"
                
            lead = {
                "id": str(uuid.uuid4()),
                "domain": unique_domain,
                "url": url,
                "businessName": profile.get("businessName", unique_domain),
                "industry": industry_val,
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
            leads_added_count += 1
            print(f"  [Saved] Lead Saved: {lead['businessName']} | {lead['phoneNumber']} | {lead['url']}")
            break
            
        time.sleep(1)
        
    print(f"\n=== Scrape Complete! Added {leads_added_count} leads to Supabase ===")

if __name__ == "__main__":
    run()
