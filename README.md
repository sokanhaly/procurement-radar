# Procurement Radar

Automated monitoring of procurement opportunities across Northeast US states (MA, VT, PA, NJ, ME, RI, CT, NY) and Maryland, scored for relevance to energy consulting practice areas and delivered by email digest and a live dashboard.

## How It Works

1. Scrapes state procurement portals daily using Playwright and Requests
2. Identifies new listings via snapshot diffing
3. Scores each listing for consulting relevance using Claude AI
4. Publishes results to a live dashboard (GitHub Pages)
5. Sends email digest of new relevant opportunities via Resend

## Dashboard

https://sokanhaly.github.io/procurement-radar/dashboard/index.html

## Schedule

Runs daily at 12:00 UTC (8:00 AM ET) via GitHub Actions. Can also be triggered manually from the Actions tab.

## Portals Monitored

- COMMBUYS (Massachusetts)
- Connecticut DAS / CTsource
- Maine Division of Procurement Services
- Rhode Island Division of Purchases
- Vermont Business Registry
- NYSTART / NYS Contract Reporter
- NJSTART (New Jersey)
- Pennsylvania eMarketplace
- Maryland eMMA

## Setup

1. Clone the repo
2. Create a `.env` file with `ANTHROPIC_API_KEY` and `RESEND_API_KEY`
3. Install dependencies: `pip install -r requirements.txt && python -m playwright install chromium`
4. Run: `python main.py`
