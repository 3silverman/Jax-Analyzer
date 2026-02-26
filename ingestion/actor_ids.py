"""
ingestion/actor_ids.py

Single source of truth for Apify actor slugs used across all ingestion modules.

Format: "owner~actor-name"  (e.g. "maxcopell~zillow-scraper")
"""

# Zillow for-sale listings and rental comps
ZILLOW_SCRAPER: str = "maxcopell~zillow-scraper"

# Furnished Finder MTR comps
FURNISHED_FINDER_SCRAPER: str = "memo23~furnishedfinder-scraper-cheerio"

# Airbnb STR comps
AIRBNB_SCRAPER: str = "tri_angle~airbnb-scraper"

# Redfin recently-sold comps (appraisal gap analysis)
REDFIN_SCRAPER: str = "epctex~redfin-scraper"
