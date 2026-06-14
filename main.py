"""
main.py
-------
The entry point. Runs the full pipeline:
  1. Load config
  2. Scrape all enabled portals
  3. Diff against snapshot -> new listings only
  4. Score new listings with Claude
  5. Merge with existing results, write results.json (dashboard)
  6. Send email digest for anything at/above threshold
  7. Update snapshot
Run locally:   python main.py
Run in CI:     called automatically by .github/workflows/scan.yml
"""
from dotenv import load_dotenv
load_dotenv()

import sys
import scraper
import score_and_diff as sd
import emailer


def main():
    print("=== Procurement Radar ===")

    # 1. Config
    config = scraper.load_config()

    # 2. Scrape
    print("\n[1/6] Scraping portals...")
    raw_listings = scraper.scrape_all(config)
    print(f"  {len(raw_listings)} total raw listings.")

    # 3. Diff
    print("\n[2/6] Diffing against snapshot...")
    seen = sd.load_snapshot()
    new_listings = sd.find_new_listings(raw_listings, seen)
    print(f"  {len(new_listings)} new listings since last scan.")

    if not new_listings:
        print("\nNothing new. Cleaning expired from dashboard and exiting.")
        # Still purge expired from dashboard even when nothing is new
        existing = sd.load_existing_results()
        cleaned = [o for o in existing if not sd._is_expired(o.get("deadline", ""))]
        if len(cleaned) < len(existing):
            print(f"  Removed {len(existing) - len(cleaned)} expired listing(s).")
            sd.write_results(cleaned)
        # Refresh snapshot in case of new IDs from changed pages
        for l in raw_listings:
            seen.add(sd.listing_fingerprint(l))
        sd.save_snapshot(seen)
        return

    # 4. Score
    print("\n[3/6] Scoring new listings with Claude...")
    scored_new = sd.score_new_listings(new_listings, config["relevance"])

    # Keep only Medium/High, and drop anything already past its deadline.
    relevant_new = [
        o for o in scored_new
        if o.get("score") in ("High", "Medium") and not o.get("expired", False)
    ]
    print(f"  {len(relevant_new)} new relevant (Medium/High, active only).")

    # 5. Merge + write dashboard data
    print("\n[4/6] Writing dashboard results...")
    existing = sd.load_existing_results()

    # Purge expired listings from existing results
    existing = [o for o in existing if not sd._is_expired(o.get("deadline", ""))]

    # De-dupe by fingerprint, newest first
    by_fp = {}
    for o in existing + relevant_new:
        fp = o.get("_fp") or sd.listing_fingerprint(o)
        by_fp[fp] = o
    merged = list(by_fp.values())

    # Sort: High before Medium, then by deadline text
    rank = {"High": 0, "Medium": 1, "Low": 2}
    merged.sort(key=lambda o: (rank.get(o.get("score"), 3), o.get("deadline", "")))
    sd.write_results(merged)

    # 6. Email
    print("\n[5/6] Sending email digest...")
    emailer.send_digest(relevant_new, config)

    # 7. Snapshot
    print("\n[6/6] Updating snapshot...")
    for l in raw_listings:
        seen.add(sd.listing_fingerprint(l))
    sd.save_snapshot(seen)

    print("\nDone.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FATAL: {e}")
        sys.exit(1)
