"""
score_and_diff.py
-----------------
1. Loads previously seen listings (snapshot.json)
2. Identifies which scraped listings are NEW
3. Sends each new listing to Claude API for relevance scoring
4. Writes results.json (for the dashboard) and updates snapshot.json
5. Returns the list of newly-relevant opportunities (for the email)

Run order in the pipeline:
    scraper.scrape_all()  ->  score_new_listings()  ->  email + dashboard
"""

import os
import json
import hashlib
from datetime import datetime, timezone

import anthropic

SNAPSHOT_FILE = "snapshot.json"
RESULTS_FILE = "dashboard/results.json"

CLAUDE_MODEL = "claude-sonnet-4-6"


# -------------------------------------------------------------------
# Snapshot handling (the "have we seen this before" memory)
# -------------------------------------------------------------------
def listing_fingerprint(listing):
    """A stable ID for a listing so we can tell new from already-seen."""
    raw = f"{listing.get('portal','')}|{listing.get('bid_id','')}|{listing.get('title','')}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def load_snapshot():
    if not os.path.exists(SNAPSHOT_FILE):
        return set()
    try:
        with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_snapshot(seen_ids):
    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen_ids), f, indent=2)


def find_new_listings(listings, seen_ids):
    new = []
    for l in listings:
        fp = listing_fingerprint(l)
        if fp not in seen_ids:
            l["_fp"] = fp
            new.append(l)
    return new


# -------------------------------------------------------------------
# Claude scoring
# -------------------------------------------------------------------
def build_prompt(listing, relevance):
    practice = "\n".join(f"- {p}" for p in relevance["practice_areas"])
    keywords = ", ".join(relevance["high_value_keywords"])
    excludes = ", ".join(relevance["exclude_keywords"])
    return f"""You are a procurement analyst for a Boston-based energy CONSULTING firm. The firm sells expert advisory, analysis, modeling, and litigation-support services. It does NOT install equipment, build facilities, supply hardware, or perform construction.

The firm's nine consulting practice areas:
{practice}

Score how well this listing matches the firm's CONSULTING services. The central test: is the buyer hiring a consultant, advisor, analyst, expert witness, or study author for energy, power, gas, or utility-related work?

STRICT SCORING RULES:

Score High ONLY if BOTH conditions are met:
  1. The project scope is clearly about energy, power, gas, utility rates, transmission, carbon, renewable energy, or fuel procurement topics
  2. The work type is consulting, advisory, analysis, modeling, expert testimony, due diligence, or feasibility study

Score Medium ONLY if the project scope appears energy or utility related AND plausibly involves advisory work, but details are limited.

Score Low in ALL of these cases:
  - The agency is an energy/utility authority BUT the project is administrative, IT, facilities, maintenance, HR, legal admin, fleet, janitorial, or general operations (e.g. a power authority hiring an IT consultant is Low)
  - Equipment purchase, installation, construction, hardware supply, or facility maintenance, even if energy-related
  - The category is "Administrative & Technical", "Information Technology", "Facilities Maintenance", or "Transportation". These are ALWAYS Low regardless of agency or title.
  - Any non-consulting procurement
  - Anything clearly about {excludes}
  - The description is missing, vague, contains junk text, or only shows a login prompt

IMPORTANT: The agency name alone does NOT determine relevance. A power authority buying office furniture or upgrading IT systems is Low. Only the actual project scope matters.

WHEN IN DOUBT, ALWAYS SCORE LOW: If you cannot confirm from the listing text that the project is specifically about energy, power, gas, utility, or fuel consulting, score Low. Never assume or speculate. Medium requires clear evidence of energy-related advisory work.

Helpful keywords (context, not sufficient alone): {keywords}

Return ONLY valid JSON, no markdown:
{{"score": "High|Medium|Low", "why": "one sentence stating ONLY what the listing actually says the project is about. Do not speculate, interpret, or add what it might involve. State only facts from the listing.", "practice_area": "matched area or none", "deadline": "extracted deadline or Not specified", "value": "extracted dollar value or Not specified"}}

LISTING:
Portal: {listing.get('portal','')}
State: {listing.get('state','')}
Title: {listing.get('title','')}
Agency: {listing.get('agency','')}
Description: {listing.get('description','')}
Raw deadline guess: {listing.get('deadline','')}
"""


def score_listing(client, listing, relevance):
    prompt = build_prompt(listing, relevance)
    try:
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            block.text for block in resp.content if block.type == "text"
        ).strip()
        text = text.replace("```json", "").replace("```", "").strip()
        data = json.loads(text)
        return data
    except Exception as e:
        print(f"    scoring error: {e}")
        return {"score": "Low", "why": "scoring failed", "practice_area": "none",
                "deadline": "Not specified", "value": "Not specified"}


def score_new_listings(new_listings, relevance):
    """Score each new listing. Returns enriched listings sorted by score."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable not set")
    client = anthropic.Anthropic(api_key=api_key)

    scored = []
    for i, listing in enumerate(new_listings, 1):
        print(f"    scoring {i}/{len(new_listings)}: {listing.get('title','')[:50]}")
        import time; time.sleep(0.5)
        verdict = score_listing(client, listing, relevance)
        deadline = verdict.get("deadline", "")
        parser_deadline = listing.get("deadline", "")
        # Prefer parser's extracted date if Claude returned nothing useful
        if not deadline or deadline == "Not specified":
            deadline = parser_deadline if parser_deadline else "Not specified"
        listing.update({
            "score": verdict.get("score", "Low"),
            "why": verdict.get("why", ""),
            "practice_area": verdict.get("practice_area", "none"),
            "deadline": deadline,
            "value": verdict.get("value", ""),
            "expired": _is_expired(deadline),
        })
        scored.append(listing)
    return scored


def _is_expired(deadline_text):
    """Return True if the deadline is in the past. Unknown dates are kept (False)."""
    if not deadline_text or deadline_text == "Not specified":
        return False
    import re
    from datetime import datetime as _dt
    today = _dt.now()
    # Try common date formats
    formats = ["%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%m-%d-%Y"]
    cleaned = deadline_text.strip()
    # Pull the first date-looking token out of a longer string
    m = re.search(r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|[A-Z][a-z]+ \d{1,2},? \d{4}|\d{4}-\d{2}-\d{2})", cleaned)
    if m:
        cleaned = m.group(1)
    for fmt in formats:
        try:
            d = _dt.strptime(cleaned, fmt)
            return d.date() < today.date()
        except ValueError:
            continue
    # Could not parse; keep it rather than wrongly dropping a live bid
    return False


# -------------------------------------------------------------------
# Output for dashboard
# -------------------------------------------------------------------
def write_results(all_relevant):
    os.makedirs("dashboard", exist_ok=True)
    payload = {
        "last_scan": datetime.now(timezone.utc).isoformat(),
        "count": len(all_relevant),
        "opportunities": all_relevant,
    }
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"  wrote {RESULTS_FILE} with {len(all_relevant)} opportunities")


def load_existing_results():
    if not os.path.exists(RESULTS_FILE):
        return []
    try:
        with open(RESULTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("opportunities", [])
    except Exception:
        return []
