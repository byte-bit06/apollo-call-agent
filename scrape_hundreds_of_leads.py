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

from outbound_crawler import gather_site_text, extract_business_profiles, normalise_au_phone
from supabase_db import db_load_leads, db_add_lead

def gemini_search_grounding(query: str, api_key: str, num_results: int = 15) -> list:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    prompt = (
        f"Search the web for '{query}'. "
        f"Find up to {num_results} real, active, distinct individual local B2B business homepage URLs in Australia. "
        f"Avoid directory lists like Yelp, YellowPages, Clutch, Hipages, Service.com.au, WordOfMouth. "
        f"Return ONLY a raw JSON list of strings (the URLs), e.g. [\"https://example.com.au\", \"https://example2.com.au\"]."
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"googleSearch": {}}]
    }
    
    max_retries = 5
    for attempt in range(1, max_retries + 1):
        print(f"  [Search] Grounding search for '{query}' (Attempt {attempt}/{max_retries})...")
        try:
            r = requests.post(url, json=payload, timeout=30)
            if r.status_code == 200:
                resp_json = r.json()
                try:
                    text = resp_json['candidates'][0]['content']['parts'][0]['text'].strip()
                except (KeyError, IndexError):
                    print("  [Search] Grounding search returned unexpected format. Retrying...")
                    time.sleep(5)
                    continue
                if text.startswith("```"):
                    text = text.split("\n", 1)[1] if "\n" in text else text
                if text.endswith("```"):
                    text = text.rsplit("\n", 1)[0] if "\n" in text else text
                text = text.strip().strip("`").strip()
                urls = json.loads(text)
                if isinstance(urls, list):
                    print(f"  [Search] Found {len(urls)} candidates.")
                    return urls
                else:
                    print("  [Search] Did not get a JSON list from Gemini.")
            elif r.status_code == 429:
                print("  [Search] Quota exceeded (429). Waiting 60 seconds before retry...")
                time.sleep(60)
            else:
                print(f"  [Search] Gemini search grounding request failed: {r.status_code} {r.text}")
                time.sleep(5)
        except Exception as e:
            print(f"  [Search] Error running Gemini search grounding: {e}")
            time.sleep(5)
            
    return []

def run():
    print("=== Scrape 100+ Leads (Real Estate, IT Support, Builders, Painters, Plumbers) ===")
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY is not configured in .env")
        return
        
    # Load existing database leads
    existing_leads = db_load_leads()
    existing_domains = {l["domain"] for l in existing_leads}
    print(f"Loaded {len(existing_leads)} existing leads.")
    
    # Define a large set of search combinations to guarantee getting hundreds of leads
    categories = [
        {"industry": "Real Estate", "queries": ["real estate agencies Melbourne", "real estate agencies Sydney", "real estate agencies Brisbane", "real estate agencies Adelaide", "real estate agencies Perth"]},
        {"industry": "IT Support", "queries": ["IT support companies Melbourne", "IT support companies Sydney", "IT support companies Brisbane", "IT support companies Adelaide", "IT support companies Perth"]},
        {"industry": "Commercial Builders", "queries": ["commercial builders Melbourne", "commercial builders Sydney", "commercial builders Brisbane", "commercial builders Adelaide", "commercial builders Perth"]},
        {"industry": "Plumbing", "queries": ["commercial plumbers Melbourne", "commercial plumbers Sydney", "commercial plumbers Brisbane", "commercial plumbers Adelaide", "commercial plumbers Perth"]},
        {"industry": "Electrical", "queries": ["commercial electricians Melbourne", "commercial electricians Sydney", "commercial electricians Brisbane", "commercial electricians Adelaide", "commercial electricians Perth"]},
        {"industry": "HVAC", "queries": ["commercial air conditioning Melbourne", "commercial air conditioning Sydney", "commercial air conditioning Brisbane", "commercial air conditioning Adelaide", "commercial air conditioning Perth"]},
        {"industry": "Painting Services", "queries": ["commercial painters Melbourne", "commercial painters Sydney", "commercial painters Brisbane", "commercial painters Adelaide", "commercial painters Perth"]}
    ]
    
    leads_added_count = 0
    target_leads = 120 # Target 120 leads to be absolutely sure we hit the "hundreds" mark
    
    # Gather candidate URLs from all search queries
    all_candidates = [] # list of (url, industry)
    seen_urls = set()
    
    print("\n--- Phase 1: Gathering Candidate URLs via Google Search Grounding ---")
    for category in categories:
        industry = category["industry"]
        for query in category["queries"]:
            # Search grounding call
            urls = gemini_search_grounding(query, api_key, num_results=15)
            for u in urls:
                try:
                    parsed = urlparse(u if u.startswith("http") else "https://" + u)
                    domain = parsed.netloc.lower().removeprefix("www.")
                    if domain and domain not in existing_domains and domain not in seen_urls:
                        seen_urls.add(domain)
                        clean_url = f"{parsed.scheme or 'https'}://{parsed.netloc}"
                        all_candidates.append((clean_url, industry))
                except Exception:
                    pass
            # Delay to comply with search grounding rate limits
            time.sleep(5)
            
    print(f"\nGathered {len(all_candidates)} unique candidate URLs to process.")
    
    print("\n--- Phase 2: Crawling & Profiling Sites (Saving to Supabase immediately) ---")
    for idx, (url, industry) in enumerate(all_candidates):
        if leads_added_count >= target_leads:
            print(f"\nReached total target of {leads_added_count} leads. Stopping.")
            break
            
        domain = urlparse(url).netloc.lower().removeprefix("www.")
        print(f"\n[{idx + 1}/{len(all_candidates)}] Processing: {domain} ({industry})...")
        
        # Crawl site content
        site_text = gather_site_text(url)
        if not site_text.strip():
            print("  Skipping (no content retrieved).")
            continue
            
        # Rate limit safety sleep before calling Gemini extraction model (stay under 15 RPM)
        print("  Waiting 4.5 seconds to comply with Gemini RPM rate limit...")
        time.sleep(4.5)
        
        profiles = extract_business_profiles(site_text, api_key=api_key)
        if not profiles:
            print("  Skipping (no phone number or valid profile).")
            continue
            
        # Save lead on the fly
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
                "industry": industry, # Use the search category industry
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
            break # Save only one main business per website
            
        time.sleep(0.5)
        
    print(f"\n=== Scrape Complete! Added {leads_added_count} leads to Supabase ===")

if __name__ == "__main__":
    run()
