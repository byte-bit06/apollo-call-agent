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

BLOCKED_DOMAINS = {
    "yelp.com", "yellowpages.com.au", "truelocal.com.au", "oneflare.com.au", 
    "hipages.com.au", "service.com.au", "serviceseeking.com.au", "facebook.com", 
    "instagram.com", "twitter.com", "linkedin.com", "youtube.com", "pinterest.com", 
    "clutch.co", "localsearch.com.au", "wordofmouth.com.au", "productreview.com.au", 
    "local.com.au", "seek.com.au", "indeed.com", "domain.com.au", "realestate.com.au", 
    "servicesaustralia.gov.au", "airbnb.com", "booking.com", "tripadvisor.com", "google.com",
    "mapquest.com", "whitepages.com.au", "truelocal.com"
}

def log(msg):
    print(f"[Trades Scraper] {msg}")

def gemini_search_grounding(query: str, api_key: str) -> list:
    """Uses Gemini Search Grounding to find local trades business sites and returns grounding redirect URLs."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    prompt = (
        f"Find 60 real, active, distinct individual local service business websites in Australia for the query: '{query}'. "
        "Avoid any directories, portals, aggregators, or social media sites. List their business names and website homepages."
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"googleSearch": {}}]
    }
    
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        log(f"  Querying Gemini search grounding for '{query}' (Attempt {attempt}/{max_retries})...")
        try:
            r = requests.post(url, json=payload, timeout=30)
            if r.status_code == 200:
                resp_json = r.json()
                try:
                    candidates = resp_json.get('candidates', [])
                    if not candidates:
                        log("  No candidates returned. Retrying...")
                        time.sleep(5)
                        continue
                        
                    metadata = candidates[0].get('groundingMetadata', {})
                    chunks = metadata.get('groundingChunks', [])
                    
                    redirect_urls = []
                    for chunk in chunks:
                        uri = chunk.get('web', {}).get('uri')
                        if uri:
                            redirect_urls.append(uri)
                            
                    log(f"  Extracted {len(redirect_urls)} redirect URLs from grounding metadata.")
                    return redirect_urls
                except Exception as e:
                    log(f"  Error parsing grounding metadata response: {e}")
            elif r.status_code == 429:
                log("  Quota exceeded (429). Waiting 60 seconds before retry...")
                time.sleep(60)
            else:
                log(f"  Gemini search grounding request failed: {r.status_code} {r.text}")
                time.sleep(5)
        except Exception as e:
            log(f"  Error running Gemini search grounding: {e}")
            time.sleep(5)
            
    return []

def resolve_redirect_url(redirect_url: str) -> str:
    """Resolves a vertexaisearch redirect URL to the final destination URL."""
    try:
        r = requests.head(redirect_url, allow_redirects=True, timeout=8)
        if r.status_code == 200:
            return r.url
    except Exception:
        # Fallback to get request if head fails
        try:
            r = requests.get(redirect_url, allow_redirects=True, timeout=8)
            if r.status_code == 200:
                return r.url
        except Exception:
            pass
    return ""

def run():
    log("=== Mass Trades Leads Scraper (Supabase On-The-Fly) ===")
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        log("Error: GEMINI_API_KEY is not configured in .env")
        return
        
    # Load existing leads to prevent duplicates
    existing_leads = db_load_leads()
    existing_domains = {l["domain"] for l in existing_leads}
    log(f"Loaded {len(existing_leads)} existing leads from Supabase.")
    
    # 5 Industries requested by the user
    target_queries = {
        "Plumbing": "local commercial plumbers in Melbourne Sydney Brisbane Adelaide Perth Hobart Australia",
        "Electrical": "local commercial electricians in Melbourne Sydney Brisbane Adelaide Perth Hobart Australia",
        "HVAC": "local air conditioning repairs and HVAC installation services in Melbourne Sydney Brisbane Adelaide Perth Hobart Australia",
        "Locksmiths": "local emergency locksmith services in Melbourne Sydney Brisbane Adelaide Perth Hobart Australia",
        "Pest Control": "local commercial pest control and termite treatment services in Melbourne Sydney Brisbane Adelaide Perth Hobart Australia"
    }
    
    # Phase 1: Gather redirect URLs from search grounding (5 calls total to avoid quota limit)
    all_redirects = [] # list of (redirect_url, industry)
    for industry, query in target_queries.items():
        redirects = gemini_search_grounding(query, api_key)
        for r_url in redirects:
            all_redirects.append((r_url, industry))
        time.sleep(5.0) # Small delay between search grounding requests
        
    log(f"\nPhase 1 Complete! Gathered {len(all_redirects)} raw grounding sources. Starting resolution...")
    
    # Phase 2: Resolve redirect URLs to final homepages
    candidate_urls = [] # list of (final_url, industry)
    seen_domains = set()
    
    for idx, (r_url, industry) in enumerate(all_redirects):
        if idx % 10 == 0:
            log(f"Resolving redirects: [{idx}/{len(all_redirects)}]...")
            
        final_url = resolve_redirect_url(r_url)
        if not final_url:
            continue
            
        try:
            parsed = urlparse(final_url)
            domain = parsed.netloc.lower().removeprefix("www.")
            
            # Check blocked directories
            if any(blocked in domain for blocked in BLOCKED_DOMAINS):
                continue
                
            if domain and domain not in existing_domains and domain not in seen_domains:
                seen_domains.add(domain)
                clean_url = f"{parsed.scheme or 'https'}://{parsed.netloc}"
                candidate_urls.append((clean_url, industry))
        except Exception:
            pass
            
    log(f"\nPhase 2 Complete! Resolved {len(candidate_urls)} clean, unique local business URLs.")
    
    # Phase 3: Crawl websites and extract profiles, saving immediately to Supabase
    log("\n--- Phase 3: Crawling & Profiling Sites (Saving to Supabase immediately) ---")
    leads_added_count = 0
    
    for idx, (url, industry) in enumerate(candidate_urls):
        domain = urlparse(url).netloc.lower().removeprefix("www.")
        log(f"\n[{idx + 1}/{len(candidate_urls)}] Crawling: {domain} ({industry})...")
        
        try:
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
