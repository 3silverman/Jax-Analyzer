# Jacksonville Real Estate Investment Analyzer — Project Spec

## Overview
A production-grade real estate investment analysis platform focused on the Jacksonville, FL market.
Ingests listings from multiple sources, normalizes them, underwrites LTR/MTR/STR strategies,
scores each deal, and delivers actionable reports via a server-rendered web UI and email digests.

## Architecture

| Layer | Technology |
|---|---|
| Backend API | Python 3.11 + FastAPI |
| Database | PostgreSQL via Supabase |
| UI | Jinja2 server-rendered templates (HTMX-friendly) |
| Scheduler | APScheduler (daily scans at 6 AM ET) |
| Deploy | Render (web service + cron job) |
| Secrets | Environment variables (.env locally, Render dashboard in prod) |

## Folder Structure
```
Jax-Analyzer/
├── ingestion/       # Scrapers & API clients (Apify, Rentcast, Zillow)
├── normalization/   # Field mapping → canonical PropertyRecord schema
├── underwriting/    # Pure math: cash flow, returns, stress tests (no API calls)
├── scoring/         # Deal score (0-100) from underwriting + neighborhood data
├── neighborhood/    # Walk Score, crime index, school ratings, flood zone
├── delivery/        # Email digest, Slack webhook, PDF report generation
├── api/             # FastAPI app, routers, dependencies, middleware
├── ui/              # Jinja2 templates, static assets
├── db/              # SQLAlchemy models, Alembic migrations, Supabase client
└── tests/           # Pytest unit + integration tests
```

## Data Sources

| Source | Purpose | Auth |
|---|---|---|
| Apify (Zillow scraper) | Active MLS listings | `APIFY_TOKEN` |
| Rentcast | Rent comps, AVM | `RENTCAST_API_KEY` |
| Walk Score | Walkability / transit | `WALKSCORE_API_KEY` |
| Google Maps | Distance to employment centers | `GOOGLE_MAPS_API_KEY` |
| Supabase | Primary database + storage | `SUPABASE_URL`, `SUPABASE_KEY` |

## Module Contracts

### ingestion/
- `fetch_listings(source: str) -> list[dict]`
- Returns raw dicts; no normalization here.

### normalization/
- `normalize(raw: dict, source: str) -> PropertyRecord`
- Canonical schema: address, price, beds, baths, sqft, year_built, lat, lon, list_date.

### underwriting/
- `underwrite(property: dict) -> UnderwritingResult`
- Returns cash-flow for all three strategies + 5 stress tests.
- **Pure math — zero API calls or DB access.**

### scoring/
- `score_deal(underwriting: UnderwritingResult, neighborhood: NeighborhoodData) -> DealScore`
- 0-100 composite: 40% returns, 30% neighborhood, 20% risk, 10% liquidity.

### neighborhood/
- `get_neighborhood_data(lat, lon, address) -> NeighborhoodData`
- Aggregates walkability, crime, schools, flood zone, drive times.

### delivery/
- `send_daily_digest(deals: list[DealScore]) -> None`
- `generate_pdf_report(deal: DealScore) -> bytes`

## Database Schema (key tables)

```sql
properties          -- raw + normalized listing data
underwriting_results -- cached underwriting output per property
deal_scores         -- composite score + metadata
neighborhood_cache  -- cached neighborhood API responses (TTL 30 days)
scan_runs           -- scheduler audit log
```

## Scheduler (APScheduler)
- Daily at 6:00 AM ET: fetch → normalize → underwrite → score → persist → digest
- Separate job: refresh neighborhood cache for top-100 deals

## Underwriting Model

### Strategies
- **LTR** – Long-Term Rental: 12-month lease, stable rent, standard vacancy
- **MTR** – Medium-Term Rental: 1–6 month furnished, corporate/travel nurses
- **STR** – Short-Term Rental: Airbnb/VRBO, ADR × occupancy revenue model

### Expense Stack (all strategies)
| Expense | LTR | MTR | STR |
|---|---|---|---|
| Vacancy | 8% | 15% | built into occupancy |
| Property Mgmt | 10% of EGI | 15% of EGI | 25–30% of revenue |
| Property Tax | actual or 1.1% of value | same | same |
| Insurance | actual or 0.5% of value | +25% landlord rider | +STR endorsement |
| Maintenance | 1% of value | 1% of value | 1.5% of value |
| CapEx Reserve | 1% of value | 1% of value | 1% of value |
| HOA | actual | actual | actual |
| Utilities | none (tenant pays) | $150–250/mo | $200–350/mo |
| Furnishing Amort | none | $100–150/mo | $150–250/mo |
| Platform Fee | none | none | 3% of revenue |
| Cleaning | none | none | per-stay cost |

### Five Stress Tests
1. **Base Case** — input assumptions as provided
2. **Rate Shock** — interest rate +200 bps
3. **Rent Decline** — gross revenue −15%
4. **Vacancy Surge** — vacancy/occupancy stressed by 50% (e.g., 8% → 12%, 65% occ → 43%)
5. **Full Stress** — rate +100 bps + revenue −10% + vacancy +5 pts combined

### Key Metrics Output (per strategy × scenario)
- Gross Annual Revenue
- Effective Gross Income (EGI)
- Total Operating Expenses
- Net Operating Income (NOI)
- Annual Debt Service
- Annual Cash Flow
- Monthly Cash Flow
- Cash-on-Cash Return (CoC)
- Cap Rate
- Gross Rent Multiplier (GRM)
- Break-Even Occupancy (STR only)
- DSCR (Debt Service Coverage Ratio)

## Scoring Rubric
| Component | Weight | Signal |
|---|---|---|
| Returns | 40% | CoC ≥ 8% = full marks; negative CoC = 0 |
| Neighborhood | 30% | Walk Score, crime, schools, flood |
| Risk | 20% | DSCR headroom, stress test resilience |
| Liquidity | 10% | Days-on-market trend, price/sqft vs comps |

## Deployment (Render)

```yaml
# render.yaml
services:
  - type: web
    name: jax-analyzer-api
    env: python
    buildCommand: pip install -r requirements.txt && alembic upgrade head
    startCommand: uvicorn api.main:app --host 0.0.0.0 --port $PORT
  - type: cron
    name: jax-analyzer-daily-scan
    env: python
    schedule: "0 11 * * *"   # 6 AM ET = 11 AM UTC
    buildCommand: pip install -r requirements.txt
    startCommand: python -m ingestion.run_scan
```

## Development Conventions
- Python 3.11+; type hints on all public functions
- `pytest` for all tests; coverage ≥ 80% on underwriting/ and scoring/
- `ruff` for linting, `black` for formatting
- All monetary values in **USD floats** (not Decimal) for speed; display rounds to 2 dp
- Property dict keys use **snake_case**
- Never call external APIs from underwriting/ or scoring/ — pure computation only
- Log with `structlog`; never print() in production code

## Environment Variables
See `.env.example` for the full list.

---
*Update this file as the spec evolves. Claude should read CLAUDE.md at the start of every session.*
