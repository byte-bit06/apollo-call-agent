"""
outbound_crawler.py
Lead Discovery Engine for the AI Sales Outbound Dialer.

- Searches DuckDuckGo (HTML scraping, no API key needed) with Australia bias.
- Accepts raw URL lists as an alternative input.
- Crawls homepage + /contact + /about pages for each discovered domain.
- Uses Gemini API to extract structured business profile:
    businessName, industry, services, painPoints, phoneNumber (AU E.164).
"""

import re
import time
import json
import logging
import requests
from urllib.parse import urlparse, urljoin, quote_plus
from bs4 import BeautifulSoup

logger = logging.getLogger("outbound_crawler")

# ---------------------------------------------------------------------------
# Australian phone number normalisation
# ---------------------------------------------------------------------------

AU_MOBILE_RE = re.compile(r"(?:(?:\+?61)|0)4\d{8}")
AU_LANDLINE_RE = re.compile(r"(?:(?:\+?61)|0)[23578]\d{8}")
AU_1X00_RE = re.compile(r"1[38]00\s?\d{6}")
ANY_PHONE_RE = re.compile(
    r"(?:\+?61|0)[234578]\d{8}|1[38]00\s?\d{6}|(?:\+?61\s?)?0?4\d{2}\s?\d{3}\s?\d{3}"
)

def normalise_au_phone(raw: str) -> str:
    """
    Attempts to normalise an Australian phone number to E.164 (+61...).
    Returns the normalised number or the original string if it cannot be normalised.
    """
    if not raw:
        return raw
    digits = re.sub(r"[\s\-\(\)\.]", "", raw)
    # Already in +61 format
    if digits.startswith("+61") and len(digits) == 12:
        return digits
    # International without +
    if digits.startswith("61") and len(digits) == 11:
        return "+" + digits
    # Local format starting with 0
    if digits.startswith("0") and len(digits) == 10:
        return "+61" + digits[1:]
    # 1300 / 1800 (keep as-is but add +61 prefix for Vapi compatibility)
    if re.match(r"^1[38]00\d{6}$", digits):
        return "+61" + digits
    return raw


def extract_phones_from_text(text: str) -> list:
    """Finds all potential Australian phone numbers in a text block."""
    raw_matches = ANY_PHONE_RE.findall(text)
    normalised = []
    seen = set()
    for m in raw_matches:
        n = normalise_au_phone(m)
        if n and n not in seen:
            seen.add(n)
            normalised.append(n)
    return normalised


# ---------------------------------------------------------------------------
# DuckDuckGo HTML search (no API key required)
# ---------------------------------------------------------------------------

AU_TERMS = [
    "australia", "australian", "sydney", "melbourne", "brisbane",
    "perth", "adelaide", "canberra", "nsw", "vic", "qld", "wa", "sa",
    "gold coast", "newcastle", "wollongong", "hobart", "darwin",
]

BLOCKED_DOMAINS = {
    "duckduckgo.com", "google.com", "bing.com", "facebook.com",
    "linkedin.com", "instagram.com", "twitter.com", "x.com",
    "youtube.com", "wikipedia.org", "yelp.com", "yellowpages.com.au",
    "truelocal.com.au", "hotfrog.com.au", "whitepages.com.au",
    "hipages.com.au", "service.com.au", "oneflare.com.au", "bark.com",
    "seek.com.au", "indeed.com", "airtasker.com", "gumtree.com.au",
    "yelp.com.au", "wordofmouth.com.au", "localbusinessguide.com.au",
    "skilledtradies.com.au", "plumbersden.com", "localsearch.com.au",
    "glassdoor.com.au", "jora.com", "careerone.com.au",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}


def _ensure_australia(query: str) -> str:
    """Appends 'Australia' to query if no location term is present."""
    if not any(t in query.lower() for t in AU_TERMS):
        return query.strip() + " Australia"
    return query.strip()


def _extract_base_url(url: str) -> str | None:
    """Returns scheme://netloc or None if the URL is from a blocked domain."""
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower().lstrip("www.")
        if not netloc or any(blocked in netloc for blocked in BLOCKED_DOMAINS):
            return None
        return f"{parsed.scheme or 'https'}://{parsed.netloc}"
    except Exception:
        return None


def _ddg_library_search(query: str, num_results: int) -> list:
    """Uses the duckduckgo_search library (most reliable, handles rate limits)."""
    try:
        from duckduckgo_search import DDGS
        urls = []
        seen = set()
        with DDGS() as ddgs:
            results = ddgs.text(query, region="au-en", max_results=num_results * 3)
            for r in results:
                href = r.get("href", "") or r.get("url", "")
                base = _extract_base_url(href)
                if base and base not in seen:
                    seen.add(base)
                    urls.append(base)
                if len(urls) >= num_results:
                    break
        return urls
    except ImportError:
        return []
    except Exception as e:
        logger.warning(f"duckduckgo_search library failed: {e}")
        return []


def _ddg_html_search(query: str, num_results: int) -> list:
    """Fallback: scrapes DuckDuckGo HTML endpoint directly."""
    try:
        resp = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query, "b": "", "kl": "au-en"},
            headers=HEADERS,
            timeout=20,
        )
        if resp.status_code != 200:
            return []
        soup = BeautifulSoup(resp.text, "html.parser")
        urls = []
        seen = set()
        # Try multiple selector patterns DDG uses
        for selector in ["a.result__url", "a.result__a", "h2.result__title a", ".results a[href^='http']"]:
            for a in soup.select(selector):
                href = a.get("href", "").strip()
                if not href.startswith("http"):
                    # DDG sometimes wraps links
                    href = a.get("data-href", href)
                base = _extract_base_url(href)
                if base and base not in seen:
                    seen.add(base)
                    urls.append(base)
                if len(urls) >= num_results:
                    return urls
        return urls
    except Exception as e:
        logger.warning(f"DDG HTML scraping failed: {e}")
        return []


def ddg_search(query: str, num_results: int = 10) -> list:
    """
    Searches DuckDuckGo for the given query and returns base URLs.
    Tries the duckduckgo_search library first, falls back to HTML scraping.
    Automatically appends 'Australia' if no location term is present.
    """
    query = _ensure_australia(query)
    logger.info(f"Searching: {query!r}")

    # Attempt 1: duckduckgo_search library (most reliable)
    urls = _ddg_library_search(query, num_results)
    if urls:
        logger.info(f"  → {len(urls)} results via library")
        return urls

    # Attempt 2: HTML scraping fallback
    time.sleep(2)
    urls = _ddg_html_search(query, num_results)
    if urls:
        logger.info(f"  → {len(urls)} results via HTML scraping")
        return urls

    logger.warning(f"  → No results found for: {query!r}")
    return []



# ---------------------------------------------------------------------------
# Site page fetcher
# ---------------------------------------------------------------------------

SUBPAGES_TO_TRY = ["/contact", "/contact-us", "/about", "/about-us", "/our-services", "/services"]


def fetch_text(url: str, timeout: int = 10) -> str:
    """Fetches a URL and returns its visible text content as plain text."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        if resp.status_code != 200:
            return ""
        ct = resp.headers.get("Content-Type", "")
        if "text/html" not in ct:
            return ""
        soup = BeautifulSoup(resp.text, "html.parser")
        for el in soup(["script", "style", "nav", "footer", "header", "aside", "iframe", "noscript"]):
            el.decompose()
        return soup.get_text(separator=" ", strip=True)
    except Exception as e:
        logger.debug(f"fetch_text({url}) failed: {e}")
        return ""


def gather_site_text(base_url: str, log_fn=None) -> str:
    """
    Fetches the homepage + up to 2 subpages (contact/about/services) and
    concatenates their text for Gemini extraction.
    """
    def _log(msg):
        logger.info(msg)
        if log_fn:
            log_fn(msg)

    _log(f"Fetching: {base_url}")
    texts = []
    home_text = fetch_text(base_url)
    if home_text:
        texts.append(home_text)

    fetched_sub = 0
    for sub in SUBPAGES_TO_TRY:
        if fetched_sub >= 2:
            break
        sub_url = base_url.rstrip("/") + sub
        sub_text = fetch_text(sub_url)
        if sub_text:
            texts.append(sub_text)
            fetched_sub += 1
        time.sleep(0.3)

    combined = "\n\n".join(texts)
    _log(f"  -> Gathered {len(combined)} characters from {base_url}")
    return combined


# ---------------------------------------------------------------------------
# Gemini extraction
# ---------------------------------------------------------------------------

EXTRACTION_PROMPT = """You are a lead research assistant. Your task is to extract one or more business leads from the raw website text below.

WEBSITE TEXT:
---
{text}
---

Analyze the text and extract all distinct business profiles present.
You MUST return a valid JSON object (no markdown, no code fences) with EXACTLY this structure:
{{
  "leads": [
    {{
      "businessName": "Name of the business",
      "industry": "Industry category (e.g. Plumbing, Legal, Accounting, Real Estate, IT Services, Marketing, Construction, Cleaning, etc.)",
      "services": "A concise 2-3 sentence summary of the main services or products they offer",
      "painPoints": "2-3 common business challenges this type of business typically faces that an AI voice assistant or booking automation could help solve (e.g., missed calls after hours, booking no-shows, high admin workload). Make this specific to their industry.",
      "phoneNumber": "Australian business phone number in E.164 format (+61XXXXXXXXX). If no Australian number is found, return empty string."
    }}
  ]
}}

Rules:
- If the website represents a single business website, return a list with exactly 1 lead.
- If the website contains a list, directory, or marketplace profiles of multiple businesses (e.g. local listings page), extract up to 10 distinct businesses listed with their names and phone numbers.
- ONLY extract businesses that have a phone number. If a business has no phone number, do NOT include it in the leads list.
- Phone numbers must be normalised to Australian format (+61...).
- Do NOT include any text outside the JSON object.
"""


def extract_business_profiles(text: str, api_key: str, log_fn=None) -> list:
    """
    Calls Gemini API via REST to extract a list of structured business profiles from raw site text.
    Returns a list of dicts.
    """
    def _log(msg):
        logger.info(msg)
        if log_fn:
            log_fn(msg)

    if not text or not text.strip():
        return []

    # Truncate to 15,000 chars to stay well within token limits
    truncated = text[:15000]
    prompt = EXTRACTION_PROMPT.format(text=truncated)

    # Try models in order: gemini-2.5-flash, gemini-1.5-flash
    models = ["gemini-2.5-flash", "gemini-1.5-flash-latest"]
    raw = None

    for model in models:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}]
            }
            res = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=15)
            if res.status_code == 200:
                data = res.json()
                raw = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                break
            else:
                _log(f"  [model] {model} failed with status {res.status_code}: {res.text}")
        except Exception as e:
            _log(f"  [model] {model} error: {e}")

    if not raw:
        _log("  [!] All Gemini models failed to extract profiles.")
        return []

    try:
        # Strip markdown code fences if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        parsed_json = json.loads(raw)
        extracted_leads = parsed_json.get("leads", [])
        if not isinstance(extracted_leads, list):
            # Fallback if it returned single object
            if isinstance(parsed_json, dict) and "businessName" in parsed_json:
                extracted_leads = [parsed_json]
            else:
                extracted_leads = []

        valid_profiles = []
        for profile in extracted_leads:
            biz_name = profile.get("businessName")
            phone = profile.get("phoneNumber")

            # Normalise phone number if present
            if phone:
                # If it's a descriptive non-number string, treat it as empty
                digits_only = re.sub(r"\D", "", str(phone))
                if len(digits_only) < 6:
                    phone = None
                else:
                    phone = normalise_au_phone(str(phone))
                profile["phoneNumber"] = phone

            # If no number from Gemini, try extracting from the raw text using regex as fallback
            # (only if we only have one business extracted, otherwise regex would mix up numbers)
            if not phone and len(extracted_leads) == 1:
                phones = extract_phones_from_text(text)
                if phones:
                    phone = phones[0]
                    profile["phoneNumber"] = phone

            # Strict rule: only add leads with a valid phone number
            if biz_name and phone:
                valid_profiles.append(profile)

        return valid_profiles

    except json.JSONDecodeError as e:
        _log(f"  [!] Gemini returned invalid JSON: {e}")
        return []
    except Exception as e:
        _log(f"  [!] Gemini extraction failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Main discovery runner
# ---------------------------------------------------------------------------

def discover_leads(
    queries: list = None,
    urls: list = None,
    results_per_query: int = 8,
    contacted_domains: set = None,
    api_key: str = "",
    log_fn=None,
    cancel_check=None,
) -> list:
    """
    Main entry point for lead discovery.

    Args:
        queries: List of search query strings (e.g. ["plumbers Brisbane", "dental clinics Sydney"]).
        urls:    List of direct website URLs to process without searching.
        results_per_query: How many DuckDuckGo results to fetch per query.
        contacted_domains: Set of domain strings to skip (already contacted).
        api_key: Gemini API key.
        log_fn:  Optional callable(str) for live logging.
        cancel_check: Optional callable() returning True to cancel the job.

    Returns:
        List of lead dicts ready for saving to leads.json.
    """
    def _log(msg):
        logger.info(msg)
        if log_fn:
            log_fn(msg)

    if contacted_domains is None:
        contacted_domains = set()

    # Collect all candidate URLs
    candidate_urls = list(urls or [])

    if queries:
        for q in queries:
            if cancel_check and cancel_check():
                _log("[!] Discovery cancelled.")
                return []
            found = ddg_search(q, num_results=results_per_query)
            candidate_urls.extend(found)
            time.sleep(1.5)  # polite delay between searches

    # Deduplicate and filter
    seen_domains = set(contacted_domains)
    unique_urls = []
    for u in candidate_urls:
        parsed = urlparse(u if u.startswith("http") else "https://" + u)
        domain = parsed.netloc.lower().lstrip("www.")
        if domain and domain not in seen_domains:
            seen_domains.add(domain)
            full = f"{parsed.scheme or 'https'}://{parsed.netloc}"
            unique_urls.append(full)

    _log(f"Processing {len(unique_urls)} unique domains...")

    leads = []
    for url in unique_urls:
        if cancel_check and cancel_check():
            _log("[!] Discovery cancelled.")
            break

        domain = urlparse(url).netloc.lower().lstrip("www.")
        _log(f"--- {domain} ---")

        site_text = gather_site_text(url, log_fn=log_fn)
        if not site_text.strip():
            _log(f"  [!] No text extracted from {url}, skipping.")
            continue

        profiles = extract_business_profiles(site_text, api_key=api_key, log_fn=log_fn)
        if not profiles:
            _log(f"  [!] No valid business profiles with phone numbers found for {url}, skipping.")
            continue

        # Build lead record for each extracted profile
        import uuid
        from datetime import datetime, timezone
        for idx, profile in enumerate(profiles):
            # If multiple businesses are found, give them custom domains/urls to prevent deduplication conflicts
            unique_domain = domain if idx == 0 else f"{profile.get('businessName','').lower().replace(' ','')}.{domain}"
            
            lead = {
                "id": str(uuid.uuid4()),
                "domain": unique_domain,
                "url": url,
                "businessName": profile.get("businessName", unique_domain),
                "industry": profile.get("industry", ""),
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
            leads.append(lead)
            _log(f"  [Saved] Lead saved: {lead['businessName']} | {lead['phoneNumber']}")
            
        time.sleep(0.5)

    _log(f"Discovery complete. {len(leads)} new leads found.")
    return leads
