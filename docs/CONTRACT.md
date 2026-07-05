# Implementation Contract

Internal design contract for the V2 implementation. Spec: `docs/spec/bam-mutual-aid-spec.md`.
Foundation files already exist and are authoritative: `bam/config.py`, `bam/db.py`,
`bam/models.py`, `bam/request_types.py`, `bam/validation.py`, `bam/sms/*`.

## Ground rules

- Python 3.10+, SQLModel sessions passed in as the first argument to every service function.
- Services never commit half-finished state: mutate, `session.add(...)`, and `session.commit()` at the end of each public function (or accept a `commit=True` kwarg pattern if a caller composes them — keep it simple, commit inside).
- Use `bam.db.utcnow()` / `bam.models.utcnow()` for time; every public service function accepts an optional `now: datetime | None = None` parameter so tests can freeze time. SQLite drops tzinfo on round-trip — when comparing datetimes loaded from the DB, treat naive values as UTC.
- JSON list columns (`languages`, `request_types`, ...) must be **reassigned**, never mutated in place (`h.languages = [*h.languages, "es"]`).
- Status changes on `Request`/`SocialServiceRequest` must go through `bam.models.apply_status_change` so `processing_date` stays correct.
- Always bump `household.updated_at` when mutating a household.
- Request types are canonical keys (see `bam/request_types.py`); intake accepts labels in any language via `normalize_type` and drops (but reports) unknown types.

## Spec interpretation decisions (already made — do not re-litigate)

1. **Intake row deletion (spec 6.1 step 6)** — we keep `FormSubmission` rows linked to the household (matching the spec's own sequence diagram and Households schema), and the privacy scrub clears their PII after processing. Rationale documented in `docs/SPEC-MAPPING.md`.
2. **Request dedup** — a household never gets a second `Open` request of the same type; intake skips (and reports) duplicates.
3. **Auto-expiration** — `Open` requests older than their window (14d default, 30d pots_pans, 14d all social services) are set to `Timeout`, **except** when the household currently has a `Booked` appointment (don't expire someone already scheduled).
4. **"No longer needs goods" (A6)** — flowchart says Timeout, the A6 table row says "mark complete"; we use `Timeout` with a note, per the flowchart.
5. **Missed appointments** — `missed_appointment_count` increments per no-show, resets to 0 on check-in; at `settings.max_missed_appointments` (2) all the household's open goods+social requests time out.
6. **Partial fulfillment (A1)** — simply don't mark the request delivered; there is no partial state.
7. **Wrong number (A5)** — set `invalid_phone_number=True` and time out all open requests.

## Modules to implement

### `bam/schemas.py` — Pydantic I/O models (plain `pydantic.BaseModel`)

- `FormSubmissionIn`: name, phone_number, email, languages, request_types, furniture_items, bed_details, furniture_acknowledgement, kitchen_items, social_service_requests, internet_access, roof_accessible, notes, street_address, city_state, zip_code. All optional except `phone_number`; list fields default to [].
- `IntakeResult`: submission_id, household_id, created_household (bool), created_request_ids, created_social_service_request_ids, skipped_duplicate_types, unknown_types, phone_valid (bool).
- `HouseholdOut`, `RequestOut`, `SocialServiceRequestOut`, `CheckinView` (household + open requests + open social service requests), `BlastReport` (sent, skipped_invalid, skipped_no_phone, failed, results list), `OutreachCandidate`, `NoShowReport` (missed_household_ids, timed_out_household_ids), `ExpirationReport` (timed_out_request_ids, timed_out_social_service_request_ids), `ScrubReport` (households_anonymized, requests_scrubbed, submissions_scrubbed), `DistroIn/DistroOut`.

### `bam/services/intake.py` (spec 6.1)

- `submit_form(session, payload: FormSubmissionIn, now=None) -> FormSubmission` — store the raw submission.
- `process_submission(session, submission_id, now=None) -> IntakeResult` — validate/normalize phone+email; find household by normalized phone, else by `phone_hash` (re-request after anonymization → restore phone/name/email on the anonymized household, clear `anonymized_at`); update-or-create household (update languages/email/name if provided); link submission; create `Request` rows per normalized goods type (dedup rule 2; furniture/bed types get the address fields) and `SocialServiceRequest` rows per service type (with internet_access/roof_accessible/address); set `processed_at`. Invalid phone → still create household with `invalid_phone_number=True` and `phone_number=None`? **No** — keep the raw digits in `phone_number` only if E.164 parse succeeded; otherwise household gets `phone_number=None`, `invalid_phone_number=True`, and dedup falls back to exact raw-string match on prior submissions' phone. Keep it deterministic and tested.
- `process_pending(session, now=None) -> list[IntakeResult]` — process all submissions with `processed_at IS NULL`.
- `intake_and_process(session, payload, now=None) -> IntakeResult` — convenience: submit + process one.

### `bam/services/outreach.py` (spec 6.2 + flowchart + 6.4)

- `build_outreach_list(session, request_types: list[str] | None = None, languages: list[str] | None = None, exclude_texted_within_days: int = 0, exclude_attended_within_days: int = 0, limit: int | None = None, now=None) -> list[OutreachCandidate]` — households with ≥1 open request (filtered to `request_types` if given: supplies match), valid phone, language overlap if `languages` given, `last_texted`/`last_attended` older than the exclusion windows, no currently-Booked appointment; ordered by oldest open request date (spec: Date of Oldest Fulfillable Request); truncated to `limit`.
- `send_text_blast(session, household_ids, template: str, provider: SMSProvider, max_messages: int | None = None, sleeper: Callable[[float], None] = time.sleep, now=None) -> BlastReport` — renders `[FIRST_NAME]` (household name) and `[REQUEST_URL]` (settings.request_form_url) per household, skips invalid/missing phones, stops at `max_messages` (default `settings.sms_max_messages`), pauses `settings.sms_batch_pause_seconds` after every `settings.sms_batch_size` sends (spec: 30 msgs then 30s), sets `last_texted = today` on success.
- `confirm_appointment(session, household_id, appointment_date: date, appointment_time: str, now=None) -> Household` — sets Booked + date + time slot.
- `record_outreach_outcome(session, household_id, outcome, note=None, now=None) -> Household` — `outcome` in `{"no_response_timeout" (A4), "wrong_number" (A5), "no_longer_needed" (A6)}`; A4/A6: time out all open requests; A5: also `invalid_phone_number=True`. Appends note to household notes.

### `bam/services/checkin.py` (spec 6.3)

- `lookup_by_phone(session, phone: str) -> CheckinView | None` — normalize the phone, find household, return open requests + open social service requests.
- `check_in(session, household_id, now=None) -> Household` — `appointment_status = Checked-in`, `last_attended = today`, reset `missed_appointment_count` to 0.
- `fulfill_requests(session, request_ids: list[int] = (), social_service_request_ids: list[int] = (), now=None) -> list` — `apply_status_change(..., DELIVERED)` on each; increment `FulfilledRequestCount` for (today, type) via `bam/services/metrics.increment_fulfilled_count`.
- `process_no_shows(session, distro_date: date, now=None) -> NoShowReport` — for every household with `appointment_date == distro_date` and `appointment_status == Booked`: set Missed, increment `missed_appointment_count`, clear appointment date+time; if count ≥ `settings.max_missed_appointments`, time out all open requests (goods + social).

### `bam/services/expiration.py` (spec 2, 4, 6.1 step 7)

- `expire_stale_requests(session, now=None) -> ExpirationReport` — per interpretation rule 3. Window measured from `request_opened_at`.

### `bam/services/privacy.py` (spec goal: hash sensitive data; background 8: PII not anonymized after fulfillment)

- `scrub_expired_pii(session, now=None, retention_days=None) -> ScrubReport` —
  1. Requests/social-service requests that are closed (Delivered/Timeout) with `processing_date` in the past: null out street_address/city_state/zip_code/geocode/address/notes.
  2. Households with **no** open requests and `updated_at` older than `retention_days` (default settings.pii_retention_days): keep `phone_hash`, null phone_number/name/email/notes, set `anonymized_at`.
  3. Form submissions already processed and older than retention: null name/phone/email/notes/address fields, set `scrubbed_at`.

### `bam/services/metrics.py` (spec 5 + Fulfilled Request Count)

- `increment_fulfilled_count(session, on_date: date, request_type: str, n: int = 1) -> FulfilledRequestCount`
- `open_request_counts(session) -> dict[str, int]` — open counts per type key **label** (use trilingual label, plus key); shape: `{"generated_at": iso, "counts": [{"type": key, "label": label, "count": n}, ...]}` returned as dict.
- `update_website_request_data(session, path: str | None = None) -> dict` — write that dict as JSON to `path` (default settings.website_data_path) and return it. This is the hourly `UpdateWebsiteRequestData` job.
- `fulfilled_counts(session, start: date | None = None, end: date | None = None) -> list[FulfilledRequestCount]`

### `bam/api/` — FastAPI app (spec 5 `send_sms` + all flows)

`bam/api/main.py`: `create_app() -> FastAPI` wiring routers + `app = create_app()`; startup calls `init_db()`. Routers in `bam/api/routes/`: `intake.py`, `outreach.py`, `checkin.py`, `jobs.py`, `distros.py`, `metrics.py`. Use `Depends(bam.db.get_session)`.

- `POST /intake/submissions` → intake_and_process, returns IntakeResult (201)
- `GET  /households/lookup?phone=` → CheckinView (404 if none)
- `POST /outreach/list` → build_outreach_list (body: filters)
- `POST /outreach/blast` (the spec's `send_sms` web function) — body: household_ids, template, max_messages; provider from `get_provider(settings)`; returns BlastReport
- `POST /households/{id}/appointment` — body: appointment_date, appointment_time → confirm_appointment
- `POST /households/{id}/outreach-outcome` — body: outcome, note
- `POST /households/{id}/checkin` → check_in
- `POST /requests/fulfill` — body: request_ids, social_service_request_ids
- `POST /distros` / `GET /distros` — create/list distros
- `POST /distros/no-shows` — body: distro_date → process_no_shows
- `POST /jobs/expire` → expire_stale_requests
- `POST /jobs/website-data` → update_website_request_data
- `POST /jobs/scrub-pii` → scrub_expired_pii
- `GET  /metrics/open-requests` → open_request_counts
- 404 with detail for unknown household/request ids; 422 comes free from Pydantic.

### `bam/cli.py`

`main()` using argparse, subcommands: `serve` (uvicorn), `init-db`, `process-intake`, `expire`, `website-data`, `scrub-pii`, `blast` (args: --template, --request-types, --languages, --limit, --max-messages, --exclude-texted-days, --exclude-attended-days, --dry-run [console provider even if twilio configured]), `no-shows --date YYYY-MM-DD`. Each opens its own `Session(get_engine())`, prints a short JSON/text report. Cron mapping (spec 5): hourly → `bam website-data`; daily → `bam expire && bam scrub-pii`.

### Tests (pytest, in `tests/`)

Shared `tests/conftest.py` (owned by the test-infra agent): in-memory SQLite `StaticPool` engine per test via `bam.db.set_engine`, `init_db`, fixtures `session`, `client` (FastAPI TestClient overriding `get_session`), `sms` (ConsoleSMSProvider), `no_sleep` fake sleeper capturing pauses, `freeze` helper for fixed datetimes, and a `make_household`/`make_request` factory. Suites: `test_intake.py`, `test_outreach.py`, `test_checkin.py`, `test_expiration_privacy.py`, `test_metrics.py`, `test_api.py`, `test_validation.py`. Every numbered flow step and A1–A6 row must map to at least one test.
