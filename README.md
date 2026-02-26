# Jacksonville Investment Property Analyzer

Automated VA loan multifamily deal-finder for Jacksonville, FL. Scans Zillow daily,
filters by neighborhood hard gates, underwrites every viable property across LTR/MTR/STR
strategies, and surfaces actionable deals with a 0–100 Deal Score.

## Quick Start

### 1. Clone and configure environment

```bash
git clone <repo-url>
cd Jax-Analyzer
cp .env.example .env
```

Edit `.env` and fill in all API keys (see **API Keys** section below).

### 2. Set up Supabase

1. Create a project at [supabase.com](https://supabase.com)
2. Copy the **Connection String** (Settings → Database → Connection String → URI)
3. Set `SUPABASE_URL` in `.env` to that connection string
4. Open the Supabase SQL Editor and run the full contents of `db/schema.sql`

### 3. Install dependencies and run locally

```bash
pip install -r requirements.txt
uvicorn api.main:app --reload
```

Open http://localhost:8000 — you'll see the Inbox tab.

### 4. Run the smoke test (no live API keys needed)

```bash
python scripts/test_property.py
```

This runs the Dellwood triplex through the full pipeline end-to-end and prints
the deal card to the terminal.

### 5. Trigger a live scan

```bash
python -m ingestion.run_scan
```

Or click **Run Scan** in the web UI.

---

## API Keys to Acquire

| Key | Where to Get | Cost |
|-----|-------------|------|
| `APIFY_TOKEN` | [apify.com](https://apify.com) → Settings → Integrations | Pay-per-use (~$5–20/scan) |
| `RENTCAST_API_KEY` | [rentcast.io](https://app.rentcast.io) → API | Free tier (500 req/mo) |
| `WALKSCORE_API_KEY` | [walkscore.com/professional/api.php](https://www.walkscore.com/professional/api.php) | Free (5,000 req/day) |
| `GOOGLE_MAPS_API_KEY` | [console.cloud.google.com](https://console.cloud.google.com) → Maps Static API | Free tier ($200 credit/mo) |

**SMTP (for email delivery):**
- Gmail: use an App Password (2FA required); SMTP_HOST=smtp.gmail.com, SMTP_PORT=587
- Or use any transactional email service (SendGrid, Mailgun, etc.)

---

## Deploy to Render

1. Push to GitHub
2. Create a new **Web Service** on Render pointing to your repo
3. Set all environment variables in the Render dashboard
4. Render will auto-deploy on push to `main`
5. The `render.yaml` also configures a daily cron job at 6:00 AM ET

Alternatively, deploy using Docker:

```bash
docker build -t jax-analyzer .
docker run -p 8000:8000 --env-file .env jax-analyzer
```

---

## Module Overview

```
ingestion/      Apify actors + Rentcast API client
normalization/  Canonical schema, confidence scoring, deduplication
neighborhood/   Walk Score, FEMA flood zone, CrimeGrade, hospital proximity
underwriting/   Pure-math VA loan cash flow models (LTR, MTR, STR)
scoring/        Return Score + Risk Score + Confidence Score → Deal Score
delivery/       Email digest + instant high-priority alerts
api/            FastAPI routes
ui/templates/   Jinja2 HTML templates
db/             Postgres schema + async repositories
```

---

## Running Tests

```bash
pytest --tb=short -q
```

All tests are offline (no API calls). Coverage ≥ 80% on `underwriting/` and `scoring/`.

---

## Neighborhood Hard Gates

A property is rejected immediately if:
- **Crime grade** below B (C, D, or F) — from CrimeGrade.org
- **Flood zone** is AE, VE, AO, AH, or any SFHA zone
- **Zip code** not in whitelist: 32204, 32205, 32206, 32207, 32210, 32211, 32217

---

## High-Priority Alert Conditions

All five conditions must be met simultaneously:
1. Conservative cash flow ≥ $500/mo
2. DSCR ≥ 1.2
3. Risk Score ≥ 18/30
4. Confidence Score ≥ 20/30
5. At least one rental strategy validated with comps
