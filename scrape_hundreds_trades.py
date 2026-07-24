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

from outbound_crawler import gather_site_text, extract_business_profiles, ddg_search
from supabase_db import db_load_leads, db_add_lead

# Set up logging / print helper
def log(msg):
    print(f"[Scraper] {msg}")

def run():
    log("=== mass Trades Lead discovery & crawler ===")
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        log("Error: GEMINI_API_KEY is not configured in .env")
        return
        
    # Load existing database leads to prevent duplicates
    existing_leads = db_load_leads()
    existing_domains = {l["domain"] for l in existing_leads}
    log(f"Loaded {len(existing_leads)} existing leads from Supabase.")
    
    # 5 Industries requested by the user
    industries = {
        "Plumbing": ["plumbers", "plumbing services", "emergency plumber"],
        "Electrical": ["electricians", "electrical contractors", "local electrician"],
        "HVAC": ["air conditioning installation", "HVAC repairs", "aircon service"],
        "Locksmiths": ["locksmith services", "emergency locksmith", "local locksmith"],
        "Pest Control": ["pest control services", "termite treatment", "pest exterminator"]
    }
    
    # Major Australian cities
    cities = [
        "Melbourne", "Sydney", "Brisbane", "Perth", "Adelaide",
        "Gold Coast", "Canberra", "Newcastle", "Wollongong", "Hobart"
    ]
    
    # Build list of queries
    queries = []
    for industry_name, terms in industries.items():
        for term in terms:
            for city in cities:
                queries.append((f"{term} {city}", industry_name))
                
    log(f"Generated {len(queries)} search query combinations.")
    
    # Phase 1: Gather candidate URLs
    candidate_urls = [] # list of (url, industry)
    seen_domains = set()
    
    log("\n--- Phase 1: Gathering Candidate URLs via DuckDuckGo Search ---")
    query_count = 0
    for query_str, industry_name in queries:
        # Limit search queries initially to avoid DDG search blocking
        if len(candidate_urls) >= 400:
            log("Gathered 400+ candidate domains. Moving to Phase 2.")
            break
            
        query_count += 1
        log(f"[{query_count}/{len(queries)}] Searching for: '{query_str}'...")
        
        try:
            urls = ddg_search(query_str, num_results=12)
            new_added = 0
            for u in urls:
                try:
                    parsed = urlparse(u if u.startswith("http") else "https://" + u)
                    domain = parsed.netloc.lower().removeprefix("www.")
                    if domain and domain not in existing_domains and domain not in seen_domains:
                        seen_domains.add(domain)
                        clean_url = f"{parsed.scheme or 'https'}://{parsed.netloc}"
                        candidate_urls.append((clean_url, industry_name))
                        new_added += 1
                except Exception:
                    pass
            log(f"  -> Added {new_added} new unique candidates. Total so far: {len(candidate_urls)}")
        except Exception as e:
            log(f"  Error searching DDG for '{query_str}': {e}")
            
        time.sleep(2.0) # Polite delay between DuckDuckGo queries
        
    log(f"\nPhase 1 Complete! Gathered {len(candidate_urls)} unique candidate B2B homepages.")
    
    # Phase 2: Crawl and extract profiles, saving immediately to Supabase
    log("\n--- Phase 2: Crawling & Profiling Sites (Saving to Supabase immediately) ---")
    leads_added_count = 0
    
    for idx, (url, industry) in enumerate(candidate_urls):
        domain = urlparse(url).netloc.lower().removeprefix("www.")
        log(f"\n[{idx + 1}/{len(candidate_urls)}] Crawling: {domain} ({industry})...")
        
        try:
            # Crawl site content
            site_text = gather_site_text(url)
            if not site_text.strip():
                log("  Skipping (no content retrieved from website).")
                continue
                
            # Stay under 15 RPM for Gemini Free Tier
            log("  Waiting 4.5 seconds to comply with Gemini RPM rate limit...")
            time.sleep(4.5)
            
            # Extract profile details
            profiles = extract_business_profiles(site_text, api_key=api_key)
            if not profiles:
                log("  Skipping (no phone number or business profile extracted).")
                continue
                
            # Process and save on the fly
            import uuid
            from datetime import datetime, timezone
            for idx_p, profile in enumerate(profiles):
                unique_domain = domain if idx_p == 0 else f"{profile.get('businessName','').lower().replace(' ','')}.{domain}"
                if unique_domain in existing_domains:
                    continue
                    
                lead = {
                    "id": str(uuid.uuid4()),
                    "domain": unique_domain,
                    "url": url,
                    "businessName": profile.get("businessName", unique_domain),
                    "industry": industry,
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
                log(f"  [Saved] {lead['businessName']} | {lead['phoneNumber']} | {lead['url']}")
                break # Extract only one primary company per domain
                
        except Exception as e:
            log(f"  Error processing {domain}: {e}")
            
        time.sleep(0.5)
        
    log(f"\n=== Mass Trades Crawl Complete! Added {leads_added_count} leads to Supabase ===")

if __name__ == "__main__":
    run()
