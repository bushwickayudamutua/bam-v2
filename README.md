# BAM Mutual Aid System V2 — reference implementation

A standalone, self-hostable implementation of the [BAM Mutual Aid System V2
specification](docs/spec/bam-mutual-aid-spec.md) (from
[bushwickayudamutua/specs PR #1](https://github.com/bushwickayudamutua/specs/pull/1)):
intake, distribution outreach, check-in, no-show handling, request
auto-expiration, PII scrubbing, and website metrics for a mutual aid group.

## Relationship to the Airtable + bam-automation stack

BAM's production system today is Airtable (database) + Fillout (multi-language
intake forms) + [bam-automation](https://github.com/bushwickayudamutua/bam-automation)
(Digital Ocean serverless functions for cron jobs and SMS). The V2 platform
research ([docs/spec/platform-research-summary.md](docs/spec/platform-research-summary.md))
recommends enhancing that stack rather than replacing it.

This repo is neither of those paths: it is a **reference implementation of the
same V2 spec** as a single Python service — SQLModel/SQLite (or Postgres)
instead of Airtable, FastAPI instead of Airtable automations, and a pluggable
SMS provider instead of Dialpad. It exists to make the spec's flows concrete
and testable end-to-end, and to serve as a self-hostable fallback if the team
ever outgrows Airtable. Airtable's lookup/rollup/formula fields are computed
in code (see `bam/models.py`), and the wide `Fulfilled Request Count` table is
normalized to one row per (date, request type).

## Quickstart

```sh
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest              # run the test suite
bam init-db         # create tables (SQLite bam.db by default)
bam serve           # API on http://127.0.0.1:8000 (docs at /docs)
```

Submit an intake form (spec 6.1) and look the household up by phone (spec 6.3):

```sh
curl -s -X POST http://127.0.0.1:8000/intake/submissions \
  -H 'Content-Type: application/json' \
  -d '{
        "phone_number": "(718) 555-0142",
        "name": "Maria",
        "languages": ["Español"],
        "request_types": ["Ollas y sartenes / Pots & Pans / 锅碗瓢盆", "soap"],
        "social_service_requests": ["internet"],
        "internet_access": ["No internet at home"],
        "roof_accessible": true
      }'

curl -s 'http://127.0.0.1:8000/households/lookup?phone=718-555-0142'
```

Request types are accepted as canonical keys (`pots_pans`, `soap`, ...) or as
any language segment of the trilingual Airtable labels — both resolve to the
same key (`bam/request_types.py`).

## Flows implemented

| Flow | Spec | What it does |
|---|---|---|
| Intake | 6.1 | Stores the raw form submission, validates/normalizes phone + email, finds-or-creates the household (reconnecting anonymized households via phone hash), creates deduplicated `Open` requests per type. |
| Outreach | 6.2 | Builds the filtered outreach list (supplies match, language, recency), sends the rate-limited templated text blast, books appointments, records phone-outreach outcomes (A4–A6). |
| Check-in | 6.3 | Phone-number lookup, check-in (resets missed count), marks requests `Delivered` and increments the fulfilled counts. |
| No-shows | 6.3 / A2–A3 | End-of-distro pass: marks booked no-shows `Missed`, clears the appointment; at the 2nd miss, times out all their open requests. |
| Expiration | 2 / 4 | Times out `Open` requests older than their window (14 days standard, 30 for pots & pans) unless the household has a booked appointment. |
| Privacy | 2 (goals) | Scrubs address/notes from closed requests past their processing date, anonymizes inactive households (keeping only the phone hash), clears PII from old processed submissions. |
| Metrics | 5 | Open request counts per type published as website JSON (`UpdateWebsiteRequestData`); fulfilled counts per (date, type). |

All flows are exposed three ways: service functions in `bam/services/`, HTTP
endpoints (see `/docs` when serving), and CLI subcommands.

## SMS providers

- **console** (default): logs messages instead of sending them; used for local
  development, tests, and `bam blast --dry-run`.
- **twilio**: set `BAM_SMS_PROVIDER=twilio` plus the three `TWILIO_*` variables
  below, and install the optional dependency: `pip install -e ".[twilio]"`.

Blast templates support the spec's placeholders `[FIRST_NAME]` and
`[REQUEST_URL]`. Sends are capped at `BAM_SMS_MAX_MESSAGES` and pause
`BAM_SMS_BATCH_PAUSE_SECONDS` after every `BAM_SMS_BATCH_SIZE` messages
(spec 6.2: 30 messages, then a 30-second delay).

## Configuration

All settings come from environment variables (`bam/config.py`):

| Variable | Default | Purpose |
|---|---|---|
| `BAM_DATABASE_URL` | `sqlite:///bam.db` | SQLAlchemy database URL (SQLite locally, Postgres in production). |
| `BAM_SMS_PROVIDER` | `console` | `console` or `twilio`. |
| `TWILIO_ACCOUNT_SID` | (empty) | Twilio credentials; required when provider is `twilio`. |
| `TWILIO_AUTH_TOKEN` | (empty) | Twilio credentials. |
| `TWILIO_FROM_NUMBER` | (empty) | Sending number for Twilio. |
| `BAM_SMS_MAX_MESSAGES` | `240` | Text blast cap per run (spec 6.2: 240 texts for ~60 appointments). |
| `BAM_SMS_BATCH_SIZE` | `30` | Messages sent before pausing. |
| `BAM_SMS_BATCH_PAUSE_SECONDS` | `30` | Pause length between batches. |
| `BAM_REQUEST_FORM_URL` | `https://forms.fillout.com/t/ivajQbwoWxus` | Value substituted for `[REQUEST_URL]` in blast templates. |
| `BAM_DEFAULT_EXPIRY_DAYS` | `14` | Standard request auto-expiration window. |
| `BAM_EXTENDED_EXPIRY_DAYS` | `30` | Pots & pans expiration window. |
| `BAM_MAX_MISSED_APPOINTMENTS` | `2` | Missed appointments before open requests time out. |
| `BAM_PII_RETENTION_DAYS` | `30` | Days of inactivity (with no open requests) before a household is anonymized. |
| `BAM_WEBSITE_DATA_PATH` | `website_request_data.json` | Output path for the website request-count JSON. |

## Cron mapping

Mirrors the spec's section 5 jobs and the bam-automation hourly/daily crons:

```cron
0 * * * *  bam website-data                # hourly: UpdateWebsiteRequestData
0 6 * * *  bam expire && bam scrub-pii     # daily: auto-expiration + PII scrub
```

The spec's web-triggered `send_sms` function is `POST /outreach/blast` (or
`bam blast` from a shell).

## CLI

| Command | Purpose |
|---|---|
| `bam serve [--host --port --reload]` | Run the API with uvicorn. |
| `bam init-db` | Create all database tables. |
| `bam process-intake` | Process all unprocessed form submissions (spec 6.1). |
| `bam expire` | Time out stale open requests (daily cron). |
| `bam website-data` | Write open request counts to the website JSON (hourly cron). |
| `bam scrub-pii` | Scrub expired PII (daily cron). |
| `bam no-shows --date YYYY-MM-DD` | End-of-distro no-show pass for that date (spec 6.3). |
| `bam blast --template "..." [--request-types --languages --limit --max-messages --exclude-texted-days --exclude-attended-days --dry-run]` | Build the outreach list and send the text blast (spec 6.2); `--dry-run` forces the console provider. |

Every non-serve command prints a JSON report to stdout.

## Project layout

```
bam/
├── api/
│   ├── main.py            # create_app() + app; startup runs init_db
│   └── routes/            # intake, outreach, checkin, distros, jobs, metrics
├── services/
│   ├── intake.py          # spec 6.1
│   ├── outreach.py        # spec 6.2 + 6.4 A4–A6
│   ├── checkin.py         # spec 6.3 incl. no-shows (A2–A3)
│   ├── expiration.py      # auto-expiration (spec 2/4)
│   ├── privacy.py         # PII scrub (privacy goal)
│   └── metrics.py         # spec 5 + Fulfilled Request Count
├── sms/
│   ├── base.py            # SMSProvider protocol, template rendering, get_provider
│   ├── console.py         # default provider (logs, keeps messages in memory)
│   └── twilio_provider.py # optional Twilio provider
├── cli.py                 # `bam` entry point
├── config.py              # env-driven settings (table above)
├── db.py                  # engine/session helpers
├── models.py              # SQLModel tables (spec 4)
├── request_types.py       # canonical request-type catalog (trilingual labels)
├── schemas.py             # Pydantic I/O models
└── validation.py          # phone/email normalization + phone hashing
docs/
├── CONTRACT.md            # internal implementation contract
├── SPEC-MAPPING.md        # spec section → code/test map + interpretation notes
└── spec/                  # the V2 spec and background documents
tests/                     # pytest suite (in-memory SQLite per test)
```

## Deviations & interpretations

The spec leaves a few points ambiguous or self-contradictory (notably intake
row deletion in 6.1 step 6, and the A6 "no longer needs goods" row). Every
interpretation this implementation makes — and the two we flag as open
questions for the BAM team — is documented in
[docs/SPEC-MAPPING.md](docs/SPEC-MAPPING.md).
