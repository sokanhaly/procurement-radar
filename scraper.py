"""
scraper.py
----------
Fetches open bid listings from each enabled portal in config.yaml.

Two methods:
  - "requests"   : simple HTTP GET + HTML parse (for static pages, RSS, simple tables)
  - "playwright" : headless browser (for JavaScript-rendered portals like COMMBUYS, NJSTART)

Each portal returns a list of listing dicts:
  {
    "portal": "COMMBUYS (Massachusetts)",
    "state": "MA",
    "bid_id": "BD-26-1234",
    "title": "Energy Storage Feasibility Study",
    "agency": "Department of Energy Resources",
    "description": "...",
    "deadline": "2026-07-14",
    "url": "https://...",
    "value": "120000"   # or "" if unknown
  }

Resilience: if one portal fails, log the error and continue with the rest.
"""

import sys
import traceback
from datetime import datetime

import yaml
import requests
from bs4 import BeautifulSoup

# Playwright is imported lazily inside the function so the script still runs
# for "requests"-only portals even if Playwright is not installed yet.


def load_config(path="config.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# -------------------------------------------------------------------
# Generic fetchers
# -------------------------------------------------------------------
def fetch_with_requests(portal):
    """Fetch a static or lightly-dynamic page with plain HTTP."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/124.0 Safari/537.36"
    }
    resp = requests.get(portal["url"], headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.text


def fetch_with_playwright(portal):
    """Fetch a JavaScript-rendered page using a headless browser."""
    from playwright.sync_api import sync_playwright

    html = ""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(portal["url"], timeout=60000, wait_until="networkidle")
        # Many Periscope/Sovra portals (COMMBUYS, NJSTART) load results into a
        # table after the page settles. Give it a moment, then grab the DOM.
        page.wait_for_timeout(4000)
        html = page.content()
        browser.close()
    return html


# -------------------------------------------------------------------
# Portal-specific fetchers (override the generic when needed)
# -------------------------------------------------------------------
def fetch_ct_iframe(portal):
    """CT bids live inside an iframe from webprocure.proactiscloud.com.
    Navigate directly to the iframe source and show all solicitations."""
    from playwright.sync_api import sync_playwright

    # Use the "View All" URL with wildcard search to get all public bids
    iframe_url = (
        "https://webprocure.proactiscloud.com/wp-web-public/"
        "#/bidboard/search?searchterm=*&customerid=51"
    )
    html = ""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(iframe_url, timeout=60000, wait_until="networkidle")
        page.wait_for_timeout(8000)
        # Try to expand results per page if there's an option
        try:
            per_page = page.locator("select[aria-label*='per page'], select[class*='page-size']")
            if per_page.count() > 0:
                per_page.first.select_option(value="100")
                page.wait_for_timeout(5000)
        except Exception:
            pass
        html = page.content()
        browser.close()
    return html


def fetch_njstart(portal):
    """NJSTART is a Periscope platform like COMMBUYS. It needs the search
    to be triggered by clicking the search button after page load."""
    from playwright.sync_api import sync_playwright

    url = "https://www.njstart.gov/bso/view/search/external/advancedSearchBid.xhtml?openBids=true"
    html = ""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, timeout=60000, wait_until="networkidle")
        page.wait_for_timeout(3000)
        # Try clicking the search button if it exists
        try:
            search_btn = page.locator("input[value='Search'], button:has-text('Search')")
            if search_btn.count() > 0:
                search_btn.first.click()
                page.wait_for_timeout(5000)
        except Exception:
            pass
        html = page.content()
        browser.close()
    return html


def fetch_md_emma(portal):
    """Maryland eMMA loads bids dynamically. Wait longer and try to
    trigger the listing display."""
    from playwright.sync_api import sync_playwright

    html = ""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(portal["url"], timeout=60000, wait_until="networkidle")
        page.wait_for_timeout(5000)
        # Try clicking a search/filter button if available
        try:
            search_btn = page.locator("input[type='submit'], button:has-text('Search'), a:has-text('Search')")
            if search_btn.count() > 0:
                search_btn.first.click()
                page.wait_for_timeout(5000)
        except Exception:
            pass
        html = page.content()
        browser.close()
    return html


def fetch_pa_emarketplace(portal):
    """PA eMarketplace search page needs the Search button clicked
    to load the solicitation list."""
    from playwright.sync_api import sync_playwright

    html = ""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(portal["url"], timeout=60000, wait_until="networkidle")
        page.wait_for_timeout(3000)
        # Click the Search button to load results
        try:
            search_btn = page.locator("input[value='Search'], button:has-text('Search'), input[type='submit']")
            if search_btn.count() > 0:
                search_btn.first.click()
                page.wait_for_timeout(5000)
        except Exception:
            pass
        html = page.content()
        browser.close()
    return html


def fetch_nystart(portal):
    """NYSTART Contract Reporter search page. Click '50' to show more
    results, then extract the page content."""
    from playwright.sync_api import sync_playwright

    html = ""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(portal["url"], timeout=60000, wait_until="networkidle")
        page.wait_for_timeout(5000)
        # Click "50" in the display count selector to show more results
        try:
            fifty_btn = page.locator("text=50").first
            if fifty_btn.is_visible():
                fifty_btn.click()
                page.wait_for_timeout(5000)
        except Exception:
            pass
        html = page.content()
        browser.close()
    return html


# -------------------------------------------------------------------
# Per-portal parsers
# -------------------------------------------------------------------

def parse_vt(html, portal):
    """Custom parser for Vermont Business Registry. Each bid is a nested
    table inside a parent <tr>. Extract title, agency, close date, and
    build a direct link to BidPreview.aspx."""
    import re
    soup = BeautifulSoup(html, "html.parser")
    listings = []
    base = "https://www.vermontbusinessregistry.com/"

    # Find all links to BidPreview.aspx - each is one bid
    for a_tag in soup.select("a[href*='BidPreview']"):
        title = a_tag.get_text(strip=True)
        if not title or len(title) < 5:
            continue

        # Extract BidID from href like "javascript:openPrintView('BidPreview.aspx?BidID=73860', ...)"
        href = a_tag.get("href", "")
        bid_id = ""
        bid_url = portal["url"]
        m = re.search(r"BidID=(\d+)", href)
        if m:
            bid_id = m.group(1)
            bid_url = f"{base}BidPreview.aspx?BidID={bid_id}"

        # Walk up to the containing row to get agency and close date
        container = a_tag.find_parent("table")
        agency = ""
        close_date = ""
        if container:
            org_span = container.find("span", id="lblOrganization")
            if org_span:
                agency = org_span.get_text(strip=True)
            date_span = container.find("span", id="lblCloseDate")
            if date_span:
                close_date = date_span.get_text(strip=True)

        # Skip duplicates (same BidID already added)
        if any(l["bid_id"] == bid_id for l in listings):
            continue

        listings.append({
            "portal": portal["name"],
            "state": portal["state"],
            "bid_id": bid_id,
            "title": title[:200],
            "agency": agency,
            "description": f"{title} | {agency} | Close: {close_date}",
            "deadline": close_date,
            "url": bid_url,
            "value": "",
        })

    return listings


def parse_nystart(html, portal):
    """Custom parser for NYS Contract Reporter. Bids are rendered as
    div cards with title in bg-primary divs, agency/category/dates
    in nested d-flex divs."""
    import re
    soup = BeautifulSoup(html, "html.parser")
    listings = []

    # Title divs have these classes
    title_divs = soup.select("div.bg-primary.text-light.fs-5")

    for title_div in title_divs:
        title = title_div.get_text(strip=True)
        if not title or len(title) < 5:
            continue

        # Walk up to the card container to find sibling info
        card = title_div.find_parent("div", class_=lambda c: c and "border" in " ".join(c) if isinstance(c, list) else False)
        if not card:
            # Try going up a few levels
            card = title_div.parent
            if card:
                card = card.parent
            if not card:
                card = title_div

        card_text = card.get_text(" ", strip=True) if card else ""

        # Extract agency
        agency = ""
        agency_div = card.find("div", string=re.compile(r"^Agency:")) if card else None
        if not agency_div:
            # Look for the pattern in d-flex divs
            for d in card.select("div.d-flex") if card else []:
                t = d.get_text(strip=True)
                if t.startswith("Agency:"):
                    agency = t.replace("Agency:", "").strip()
                    break

        # Extract category
        category = ""
        for d in card.select("div.d-flex") if card else []:
            t = d.get_text(strip=True)
            if t.startswith("Category:"):
                category = t.replace("Category:", "").strip()
                break

        # Extract CR number
        cr_num = ""
        m = re.search(r"CR#:(\d+)", card_text)
        if m:
            cr_num = m.group(1)

        # Extract due date
        due_date = ""
        m = re.search(r"Due date:(\d{1,2}/\d{1,2}/\d{4})", card_text)
        if m:
            due_date = m.group(1)

        # Extract note/description
        note = ""
        note_div = card.select_one("div.alert-warning") if card else None
        if note_div:
            note = note_div.get_text(strip=True)[:500]

        # Build URL
        bid_url = f"https://www.nyscr.ny.gov/Ads/Details/{cr_num}" if cr_num else portal["url"]

        # Skip duplicates
        if any(l["title"] == title for l in listings):
            continue

        description = f"{title} | {agency} | {category}"
        if note:
            description += f" | {note}"

        listings.append({
            "portal": portal["name"],
            "state": portal["state"],
            "bid_id": cr_num,
            "title": title[:200],
            "agency": agency,
            "description": description[:1000],
            "deadline": due_date,
            "url": bid_url,
            "value": "",
        })

    return listings


def parse_ct_iframe(html, portal):
    """Parse the CT webprocure iframe content. The bid board uses Angular
    and renders a table or card list of solicitations."""
    soup = BeautifulSoup(html, "html.parser")
    listings = []

    # Strategy 1: look for table rows with bid data
    for row in soup.select("table tr"):
        cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
        if len(cells) < 2:
            continue
        if not any(len(c) > 10 for c in cells):
            continue
        link = row.find("a", href=True)
        text_blob = " | ".join(cells)
        listings.append({
            "portal": portal["name"],
            "state": portal["state"],
            "bid_id": cells[0][:60] if cells else "",
            "title": _longest_cell(cells),
            "agency": "",
            "description": text_blob[:1000],
            "deadline": _guess_date(text_blob),
            "url": _abs_url("https://webprocure.proactiscloud.com", link["href"]) if link else portal["url"],
            "value": "",
        })

    # Strategy 2: look for divs/spans with bid-like content (Angular rendered)
    if not listings:
        for div in soup.select("div[class*='bid'], div[class*='solicitation'], div[class*='card'], div[class*='row']"):
            text = div.get_text(strip=True)
            if len(text) < 20:
                continue
            link = div.find("a", href=True)
            listings.append({
                "portal": portal["name"],
                "state": portal["state"],
                "bid_id": "",
                "title": text[:200],
                "agency": "",
                "description": text[:1000],
                "deadline": _guess_date(text),
                "url": _abs_url("https://webprocure.proactiscloud.com", link["href"]) if link else portal["url"],
                "value": "",
            })

    # Strategy 3: fall back to generic link extraction
    if not listings:
        for a in soup.select("a[href]"):
            text = a.get_text(strip=True)
            if len(text) < 15:
                continue
            parent_text = a.find_parent().get_text(" ", strip=True) if a.find_parent() else text
            listings.append({
                "portal": portal["name"],
                "state": portal["state"],
                "bid_id": "",
                "title": text[:200],
                "agency": "",
                "description": parent_text[:1000],
                "deadline": _guess_date(parent_text),
                "url": _abs_url("https://webprocure.proactiscloud.com", a["href"]),
                "value": "",
            })

    filtered = [l for l in listings if _looks_like_bid(l.get("title", ""), l.get("description", ""))]
    return filtered


# -------------------------------------------------------------------
# Generic parser (fallback for portals without a custom parser)
# -------------------------------------------------------------------
def parse_generic(html, portal):
    """
    Best-effort generic parser. Pulls candidate listings from common patterns:
      - <table> rows
      - <div> or <li> blocks containing a link plus nearby text
    Returns a list of raw listing dicts. Over-capture is fine; Claude filters.
    """
    soup = BeautifulSoup(html, "html.parser")
    listings = []

    # Strategy 1: table rows
    for row in soup.select("table tr"):
        cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
        if len(cells) < 2:
            continue
        link = row.find("a", href=True)
        text_blob = " | ".join(cells)
        # Skip header rows / empty rows
        if not any(len(c) > 8 for c in cells):
            continue
        listings.append({
            "portal": portal["name"],
            "state": portal["state"],
            "bid_id": cells[0][:60],
            "title": _longest_cell(cells),
            "agency": "",
            "description": text_blob[:1000],
            "deadline": _guess_date(text_blob),
            "url": _abs_url(portal["url"], link["href"]) if link else portal["url"],
            "value": "",
        })

    # Strategy 2: link blocks (for sites that don't use tables)
    if not listings:
        for a in soup.select("a[href]"):
            text = a.get_text(strip=True)
            if len(text) < 15:
                continue
            parent_text = a.find_parent().get_text(" ", strip=True) if a.find_parent() else text
            listings.append({
                "portal": portal["name"],
                "state": portal["state"],
                "bid_id": "",
                "title": text[:200],
                "agency": "",
                "description": parent_text[:1000],
                "deadline": _guess_date(parent_text),
                "url": _abs_url(portal["url"], a["href"]),
                "value": "",
            })

    # Drop navigation links and page furniture; keep only real-looking bids.
    filtered = [l for l in listings if _looks_like_bid(l.get("title", ""), l.get("description", ""))]
    return filtered


def _longest_cell(cells):
    return max(cells, key=len)[:200] if cells else ""


# Phrases that mean a row is website navigation / page furniture, not a bid.
# If the title is (almost) exactly one of these, or very short and generic,
# we drop it before it ever reaches Claude. Saves cost and noise.
_NAV_JUNK = {
    "skip to content", "skip to main content", "report an accessibility issue",
    "read the update", "create an account", "forgot password", "business registry",
    "policies and disclaimers", "human resources", "boards & commissions",
    "meet the commissioner", "organizational chart", "for state employees",
    "view our rss feed", "agency services", "business office", "employee services",
    "legislative affairs", "digests & reports", "supplier registration",
    "buyer registration", "all solicitations", "solicitations", "description:",
    "first name:", "last name:", "duration:", "date prepared:", "go to awards",
    "go to contracts", "go to solicitation tabulations", "opening location:",
    "department/agency:", "amended date:", "no. of addendums:", "website disclaimer",
    "office of the governor", "contracts systems", "statewide financial system",
    "ny small business", "do business with new york", "doing business with nys",
    "advertise opportunities", "view more opportunities", "next anticipated rfp release",
    "das construction services library", "state contracting portal", "business access",
    "contractor prequalification", "school construction grants",
    "school construction grant resources", "apply for school construction grants",
    "reemployment, transfer, & reinstatement services", "ctsource bid board",
    "bid board resources", "bid board user guide",
    "supplier solicitation response and addenda guide", "ctsource contract board",
    "contract board resources", "contract board user guide",
    "new contract administrative fee notice effective j",
    "faq contract administrative fee assessment effecti",
    "vendor training and guides for osp system", "procurement statutes and regulations",
    "agency procurement campus", "agency procurement library",
    "proc 301 - contract managers", "proc 401 - solicitation",
    "mpa contract board search", "read this communication",
    "get the vendor notice here", "view the faqs here", "learn how to bid in osp",
    "frequently asked questions", "vermont apex accelerator",
    "new york statecontract reporter",
    "clear filterapply filter",
    "sort : asc", "sort : desc",
}

# Strong signals that a row IS a real solicitation.
_BID_SIGNALS = [
    "rfp", "rfq", "ifb", "rfi", "solicitation", "bid ", "bid#", "bid #",
    "request for", "invitation", "procurement", "contract", "services",
    "proposal", "bd-", "mpa ", "construction", "consulting", "supply",
    "maintenance", "study", "assessment", "advisory", "energy", "system",
]


def _looks_like_bid(title, description=""):
    """Heuristic gate: keep real solicitations, drop nav links and furniture."""
    t = (title or "").strip().lower()
    if not t or len(t) < 8:
        return False
    # Exact navigation phrases
    if t in _NAV_JUNK:
        return False
    # Partial match on junk phrases (catches truncated versions)
    for junk in _NAV_JUNK:
        if t == junk[:len(t)] and len(t) > 15:
            continue  # only block exact or near-exact matches
        if t == junk:
            return False
    # Pure menu words ending in a colon (form labels) or "toggle child menu"
    if t.endswith(":") or "toggle child menu" in t:
        return False
    # "Close Date:" fragments from VT
    if t.startswith("close date:"):
        return False
    # "Sort results by:" header
    if t.startswith("sort results by:"):
        return False
    # "Search Results -" header
    if t.startswith("search results -"):
        return False
    # CT webprocure sidebar filters
    if t.startswith("organizationselect organization"):
        return False
    if t.startswith("bid typesdas"):
        return False
    if t.startswith("commodities("):
        return False
    if t.startswith("organization("):
        return False
    if t.startswith("clear filter"):
        return False
    # PA eMarketplace page furniture
    if t.startswith("solicitationsadvertisement"):
        return False
    if t.startswith("advertisement informationgeneral"):
        return False
    if t.startswith("general informationdepartment"):
        return False
    if t.startswith("servicematerials"):
        return False
    if t == "service & materials":
        return False
    if t.startswith("solicitation informationbids must"):
        return False
    if t.startswith("related solicitation files"):
        return False
    if t.startswith("go to solicitation tabulations"):
        return False
    # Language picker entries like "Chinese (Simplified)"
    if t.endswith(")") and len(t) < 30 and "(" in t and not any(
        s in t for s in ("rfp", "rfq", "ifb", "bid", "service")
    ):
        return False
    blob = (t + " " + (description or "").lower())
    # Keep if it has a real bid signal word...
    if any(sig in blob for sig in _BID_SIGNALS):
        return True
    # ...or a bid-ID-like pattern (e.g. BD-26-..., 2026-013, RFQ 2027-09)...
    import re
    if re.search(r"\b[a-z]{0,4}[-#]?\d{2,4}[-/]\d{2,4}", blob):
        return True
    # ...or a date (real postings almost always carry one).
    if re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", blob):
        return True
    return False


def _guess_date(text):
    """Very light date sniffing; Claude will re-extract precisely later."""
    import re
    m = re.search(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b", text)
    if m:
        return m.group(1)
    m = re.search(r"\b([A-Z][a-z]+ \d{1,2},? \d{4})\b", text)
    return m.group(1) if m else ""


def _abs_url(base, href):
    from urllib.parse import urljoin
    return urljoin(base, href)


# -------------------------------------------------------------------
# Router: pick the right fetcher + parser for each portal
# -------------------------------------------------------------------
# Maps portal names to custom (fetcher, parser) pairs.
_CUSTOM_HANDLERS = {
    "Vermont Bid Opportunities": ("playwright", parse_vt),
    "Connecticut DAS / BizNet": (fetch_ct_iframe, parse_ct_iframe),
    "NJSTART (New Jersey)": (fetch_njstart, None),   # custom fetch, generic parse
    "Maryland eMaryland Marketplace Advantage (eMMA)": (fetch_md_emma, None),
    "Pennsylvania eMarketplace": (fetch_pa_emarketplace, None),
    "NYSTART / NYS Contract Reporter": (fetch_nystart, parse_nystart),
}


# -------------------------------------------------------------------
# Orchestration
# -------------------------------------------------------------------
def scrape_all(config):
    """Run every enabled portal. Return a flat list of all listings."""
    all_listings = []
    for portal in config["portals"]:
        if not portal.get("enabled", False):
            continue
        if not portal.get("url"):
            print(f"  SKIP {portal['name']} (no URL set)")
            continue
        try:
            print(f"  Scraping {portal['name']} via {portal['method']} ...")
            handler = _CUSTOM_HANDLERS.get(portal["name"])

            if handler:
                custom_fetch, custom_parse = handler
                # Fetch
                if callable(custom_fetch):
                    html = custom_fetch(portal)
                elif custom_fetch == "playwright":
                    html = fetch_with_playwright(portal)
                else:
                    html = fetch_with_requests(portal)
                # Parse
                if custom_parse:
                    rows = custom_parse(html, portal)
                else:
                    rows = parse_generic(html, portal)
            else:
                # Default path
                if portal["method"] == "playwright":
                    html = fetch_with_playwright(portal)
                else:
                    html = fetch_with_requests(portal)
                rows = parse_generic(html, portal)

            print(f"    -> {len(rows)} raw listings")
            all_listings.extend(rows)
        except Exception as e:
            print(f"    !! ERROR on {portal['name']}: {e}")
            traceback.print_exc()
            # Continue with the next portal; never crash the whole run.
            continue
    return all_listings


if __name__ == "__main__":
    cfg = load_config()
    results = scrape_all(cfg)
    print(f"\nTotal raw listings across all portals: {len(results)}")
    # Quick preview
    for r in results[:10]:
        print(f"  [{r['state']}] {r['title'][:70]}")
