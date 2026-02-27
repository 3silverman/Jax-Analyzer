# Jacksonville Investment Property Analyzer

## What This Is
Automated real estate deal-finding tool for VA loan multifamily investing. Scans Jacksonville
listings daily, filters bad neighborhoods, underwrites every viable property across LTR/MTR/STR
strategies, and surfaces actionable deals.

## Core Principle
Eliminate 95% of listings fast. Produce decision-grade underwriting on the 5% that pass.
Score only on the conservative case. Never surface a deal with low data confidence.

## Stack
| Layer | Technology |
|---|---|
| Backend | Python 3.11+ |
| API | FastAPI |
| Database | Postgres via Supabase |
| UI | Jinja2 templates (server-rendered) |
| Scheduler | APScheduler (daily scan jobs) |
| Deploy | Render |

## Module Structure
```
jax-analyzer/
├── ingestion/      # Apify actors: Zillow listings, Zillow rentals, Furnished Finder
├── normalization/  # Canonical IDs, deduplication, data confidence scoring
├── underwriting/   # Pure math: cash flow models per strategy, stress tests
├── scoring/        # Return Score, Risk Score, Confidence Score → Deal Score
├── neighborhood/   # Neighborhood Liveability Index, crime grade, Walk Score,
│                   #   flood zone, hospital proximity, Street View visual scoring
├── delivery/       # Email digest, instant alerts (score ≥ 85), deal cards
├── api/            # FastAPI routes
├── ui/             # Jinja2 templates: Inbox, Rejected, Shortlist, Settings,
│                   #   Assumptions, Audit Log tabs
└── db/             # Supabase schema, migrations
```

## Data Sources
| Source | Purpose | Auth |
|---|---|---|
| Apify | Zillow for-sale listings + rental comps + Furnished Finder MTR comps | `APIFY_TOKEN` |
| Rentcast API | Property data, tax records, ownership history | `RENTCAST_API_KEY` |
| Walk Score API | Walkability / transit / bike scores | `WALKSCORE_API_KEY` |
| CrimeGrade.org | Neighborhood crime grades (A–F) | scrape |
| Google Maps API | Street View images + satellite links | `GOOGLE_MAPS_API_KEY` |
| FEMA flood zone | Property-level flood risk | public |

## Neighborhood Hard Gates
Property moves to **Failed Gates** tab if it fails any gate:

| Gate | Requirement |
|---|---|
| Crime grade | B or above (CrimeGrade.org) |
| Flood zone | X or shaded X only (no AE/VE) |
| Zip whitelist | 32204, 32205, 32206, 32207 |

Visual scoring runs only on financially viable deals (avoids wasting API calls).

## Underwriting Model

### Loan Structure — VA Loan
- **0% down payment**
- **2.15% funding fee** rolled into loan (first use; 3.3% for subsequent use)
- Current market interest rate
- Fixed costs: P&I + property tax (0.77% Duval County) + insurance estimate

### Strategy Expense Stacks

#### LTR — Long-Term Rental
| Expense | Rate |
|---|---|
| Vacancy | 8% |
| Property management | 8% of EGI |
| Maintenance reserve | 1% of value/year |
| CapEx reserve | 0.5% of value/year |

#### MTR — Medium-Term Rental (furnished, corporate/travel-nurse, 1–6 month stays)
| Expense | Rate |
|---|---|
| Vacancy | 10% |
| Utilities + internet | $150/mo/unit |
| Furnishing amortization | $50/mo/unit |
| Turnover cost | $200/stay |

#### STR — Short-Term Rental (Airbnb / VRBO)
| Expense | Rate |
|---|---|
| Vacancy | 25% (occupancy = 75%) |
| Platform fees | 15% of gross revenue |
| Cleaning | $125/turn |
| Maintenance reserve | 1.5% of value/year (higher wear) |
| Seasonality volatility | flagged (not scored) |

### Five Stress Tests (run on every deal, every strategy)
| # | Name | Modification |
|---|---|---|
| 1 | Rate Shock | Interest rate +1% |
| 2 | Insurance Spike | Insurance cost +50% |
| 3 | Vacancy Shock | Vacancy +20 percentage points |
| 4 | Tax Reassessment | Property tax +25% |
| 5 | Year-1 Major Repair | $8,000 one-time hit to cash flow |

## Scoring System

**Deal Score = Return Score + Risk Score + Confidence Score (max 100)**

| Component | Weight | Signal |
|---|---|---|
| Return Score | 0–40 | Cash flow, DSCR, CoC — **conservative case only** |
| Risk Score | 0–30 | Property age/systems, flood, insurance volatility, appraisal risk, tenant situation |
| Confidence Score | 0–30 | Data completeness, scrape freshness, field verification |

### High-Priority Alert Conditions (ALL must be met)
- Conservative cash flow ≥ $500/mo post-PCS
- DSCR ≥ 1.2
- Risk Score ≥ 18/30
- Confidence Score ≥ 20/30
- At least one strategy fully validated with comps

## UI Tabs
| Tab | Purpose |
|---|---|
| **Inbox** | New deals ranked by Deal Score |
| **Rejected** | Failed Gates tab showing why each property was discarded |
| **Shortlist** | Deals marked for further review |
| **Settings** | Zip whitelist, alert thresholds |
| **Assumptions** | Adjustable rate, insurance, vacancy, PM%, CapEx — instant recalc |
| **Audit Log** | Full history of every scan, score, and decision |

## Deal Card Output Format
Each flagged property shows:
- Address, price, unit mix, days on market
- Neighborhood scores: CrimeGrade, Walk Score, flood zone, hospital proximity
- Strategy rankings: #1 / #2 / #3 with conservative cash flow per strategy
- VA loan snapshot: P&I, funding fee, total PITI
- Stress test results: conservative / base / optimistic
- Key risks flagged
- Street View thumbnail + satellite link
- "Why this scored high" plain-English explanation
- Deal Score (0–100) with component breakdown
- Outcome tracking: pursued / rejected / offer made / closed

## Additional Scoring Inputs

### MTR Hospital Proximity Score
| Distance to St. Vincent's or UF Health Shands | Points |
|---|---|
| 0.0–0.3 mi | +8 pts |
| 0.3–0.75 mi | +5 pts |
| 0.75–1.5 mi | +2 pts |
| 1.5 mi+ | 0 pts |

### Deal Card Extras (displayed, not scored)
- **Refinance scenario**: shown at 5.5% as a separate display field
- **House-hack out-of-pocket cost**: down payment + closing costs + reserves

## Personal Context
| Field | Value |
|---|---|
| Buyer | Naval aviator, VA loan eligible, 0% down |
| Income | ~$5,300/mo |
| Timeline | Arriving Jacksonville October 2025 for FRS training at VP-30 |
| Use case | House-hack while stationed JAX; convert to rental at next PCS |
| Target neighborhoods | Riverside (32204), Avondale (32205), Springfield (32206), San Marco (32207) |
| Hospital anchors (MTR) | St. Vincent's Medical Center, UF Health Shands JAX |
| Military anchor | NAS Jacksonville |
| Preferred property types | Duplex, triplex, quadplex, SFR with ADU |

## Build Sequence
| Session | Module | Description |
|---|---|---|
| 1 ✅ | scaffold + underwriting | Project scaffold, pure-math underwriting engine |
| 2 | ingestion + normalization | Apify actors, canonical schema, deduplication |
| 3 | neighborhood | Walk Score, flood zone, crime grade filter |
| 4 | scoring | Return / Risk / Confidence → Deal Score |
| 5 | api + ui | FastAPI routes, Jinja2 templates |
| 6 | delivery | Email digest, instant alerts |
| 7 | db + deploy | Supabase schema, Alembic migrations, Render deploy |

## Development Conventions
- Python 3.11+; type hints on all public functions
- `pytest` for all tests; coverage ≥ 80% on `underwriting/` and `scoring/`
- `ruff` for linting, `black` for formatting
- All monetary values in **USD floats** (not Decimal); display rounds to 2 dp
- Property dict keys use **snake_case**
- **Never call external APIs from `underwriting/` or `scoring/`** — pure computation only
- Log with `structlog`; never `print()` in production code
- Scores are always **conservative case** — never surface optimistic numbers as primary metric

---
*Claude: read this file at the start of every session before writing any code.*
