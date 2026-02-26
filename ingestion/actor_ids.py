"""
ingestion/actor_ids.py

Single source of truth for Apify actor slugs used across all ingestion modules.
Fill in the correct slugs from Apify Store before running scans.

Format: "owner/actor-name"  (e.g. "lukaskrivka/zillow-scraper")
"""

# Zillow for-sale listings and rental comps
ZILLOW_SCRAPER: str = ""

# Furnished Finder MTR comps
FURNISHED_FINDER_SCRAPER: str = ""

# Airbnb STR comps
AIRBNB_SCRAPER: str = ""

# Redfin recently-sold comps (appraisal gap analysis)
REDFIN_SCRAPER: str = ""
