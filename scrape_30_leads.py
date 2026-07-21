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
from supabase_db import db_load_leads, db_save_leads, db_add_lead

def gemini_search(query: str, api_key: str, num_results: int = 15) -> list:
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
    
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        print(f"  [Search] Grounding search for '{query}' (Attempt {attempt}/{max_retries})...")
        try:
            r = requests.post(url, json=payload, timeout=30)
            if r.status_code == 200:
                text = r.json()['candidates'][0]['content']['parts'][0]['text'].strip()
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
    print("=== Scrape 30 Leads (Plumbing, Electrical, HVAC) ===")
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY is not configured in .env")
        return
        
    # Load existing database leads
    existing_leads = db_load_leads()
    existing_domains = {l["domain"] for l in existing_leads}
    print(f"Loaded {len(existing_leads)} existing leads.")
    
    categories = {
        "Plumbing": "plumbing services Melbourne",
        "Electrical": "electricians Melbourne",
        "HVAC": "air conditioning repairs Melbourne"
    }
    
    leads_to_add = []
    
    for category_name, query in categories.items():
        print(f"\n--- Scraping Category: {category_name} ---")
        
        # Get search results using search grounding
        candidate_urls = gemini_search(query, api_key, num_results=18)
        
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
        
        category_leads = []
        for url in unique_urls:
            if len(category_leads) >= 10:
                print(f"Reached goal of 10 leads for {category_name}!")
                break
                
            domain = urlparse(url).netloc.lower().lstrip("www.")
            print(f"  Fetching site content: {domain}...")
            
            site_text = gather_site_text(url)
            if not site_text.strip():
                print("    Skipping (no content retrieved).")
                continue
                
            # Small rate-limit delay before calling Gemini API (stay under 15 RPM)
            print("    Waiting 4 seconds to comply with Gemini RPM rate limit...")
            time.sleep(4)
            
            profiles = extract_business_profiles(site_text, api_key=api_key)
            if not profiles:
                print("    Skipping (no phone number or valid profile).")
                continue
                
            # Save the lead
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
                    "industry": category_name, # Overwrite with exact category
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
                
                category_leads.append(lead)
                existing_domains.add(unique_domain)
                leads_to_add.append(lead)
                
                # Save immediately to Supabase
                db_add_lead(lead)
                print(f"    [Saved] Lead Saved: {lead['businessName']} | {lead['phoneNumber']} | {lead['url']}")
                break # Only need one business per site
                
            # Small delay between websites
            time.sleep(1)
            
        print(f"Category {category_name} completed with {len(category_leads)} leads.")
        
    print("\n=== Migration Complete! ===")

if __name__ == "__main__":
    run()
