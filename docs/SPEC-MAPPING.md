# Spec → implementation mapping

Where each section of [the V2 spec](spec/bam-mutual-aid-spec.md) lives in this
codebase, and how the ambiguous parts were interpreted. Companion to the
internal [CONTRACT.md](CONTRACT.md).

## Section map

| Spec section | Requirement | Module(s) | Test file(s) |
|---|---|---|---|
| 2 — Goals: deduplicate requests | No second `Open` request of the same type per household | `bam/services/intake.py` (dedup in `process_submission`) | `tests/test_intake.py` |
| 2 — Goals: data privacy (hash sensitive data) | Phone hashing, PII scrub, household anonymization | `bam/validation.py` (`hash_phone`), `bam/services/privacy.py` | `tests/test_expiration_privacy.py` |
| 2 — Goals: auto-expire stale requests (14/30 days) | `Open` requests time out after their window | `bam/services/expiration.py`, windows in `bam/request_types.py` + `bam/config.py` | `tests/test_expiration_privacy.py` |
| 2 — Goals: track fulfilled vs outstanding | Fulfilled counts + open request counts | `bam/services/metrics.py` | `tests/test_metrics.py` |
| 2 — Goals: 60 appointments / 25% confirmation | 240-message blast cap default | `bam/config.py` (`sms_max_messages`), `bam/services/outreach.py` | `tests/test_outreach.py` |
| 4 — Households table | `Household` model incl. validation flags, appointment fields, `last_texted` | `bam/models.py` | `tests/test_intake.py`, `tests/test_checkin.py` |
| 4 — Requests table | `Request` model; `Request Opened At`, address fields | `bam/models.py` | `tests/test_intake.py` |
| 4 — Processing Date formula (+14 / +30 pots & pans) | Computed on status change | `bam/models.py` (`apply_status_change`) | `tests/test_checkin.py`, `tests/test_expiration_privacy.py` |
| 4 — Social Service Requests table | `SocialServiceRequest` model incl. internet access / roof fields | `bam/models.py` | `tests/test_intake.py` |
| 4 — Distros table | `Distro` model + create/list endpoints | `bam/models.py`, `bam/api/routes/distros.py` | `tests/test_api.py` |
| 4 — Fulfilled Request Count table | Normalized to one row per (date, type) | `bam/models.py` (`FulfilledRequestCount`), `bam/services/metrics.py` | `tests/test_metrics.py` |
| 4 — Assistance Request Form Submissions table | `FormSubmission` model linked to household | `bam/models.py`, `bam/services/intake.py` | `tests/test_intake.py` |
| 4 — Status values | `RequestStatus`, `AppointmentStatus` enums | `bam/models.py` | all suites |
| 5 — `UpdateWebsiteRequestData` (hourly cron) | Open request counts written as JSON | `bam/services/metrics.py` (`update_website_request_data`), `bam/cli.py` (`bam website-data`), `POST /jobs/website-data` | `tests/test_metrics.py`, `tests/test_api.py` |
| 5 — `send_sms` (web-triggered) | Templated, rate-limited text blast | `bam/services/outreach.py` (`send_text_blast`), `POST /outreach/blast`, `bam blast` | `tests/test_outreach.py`, `tests/test_api.py` |
| 6.1 — Intake processing (steps 1–7 + sequence diagram) | Submission storage, validation, household find-or-create, request creation, expiration scheduling | `bam/services/intake.py`, `bam/validation.py`, `bam/api/routes/intake.py` | `tests/test_intake.py`, `tests/test_validation.py` |
| 6.2 — Outreach flow (steps 1–5 + flowchart) | Outreach list, text blast, appointment confirmation, phone-outcome recording | `bam/services/outreach.py`, `bam/api/routes/outreach.py` | `tests/test_outreach.py` |
| 6.3 — Check-in flow (steps 1–5) | Phone lookup, check-in, fulfillment, fulfilled-count updates | `bam/services/checkin.py`, `bam/services/metrics.py`, `bam/api/routes/checkin.py` | `tests/test_checkin.py` |
| 6.3 — No-show sequence | Mark `Missed`, clear appointment, timeout at 2nd miss | `bam/services/checkin.py` (`process_no_shows`), `POST /distros/no-shows`, `bam no-shows` | `tests/test_checkin.py` |
| 6.4 A1 — Partial fulfillment | Unfulfilled requests simply stay `Open` (no partial state) | `bam/services/checkin.py` (`fulfill_requests`) | `tests/test_checkin.py` |
| 6.4 A2 — 1st missed appointment | `Missed`, count = 1, back in the outreach queue | `bam/services/checkin.py` (`process_no_shows`), `bam/services/outreach.py` (`build_outreach_list`) | `tests/test_checkin.py`, `tests/test_outreach.py` |
| 6.4 A3 — 2nd missed appointment | All open requests → `Timeout` | `bam/services/checkin.py` (`process_no_shows`) | `tests/test_checkin.py` |
| 6.4 A4 — No response after phone calls | `record_outreach_outcome("no_response_timeout")` → open requests time out | `bam/services/outreach.py` | `tests/test_outreach.py` |
| 6.4 A5 — Wrong number | `record_outreach_outcome("wrong_number")` → `invalid_phone_number=True` + timeout | `bam/services/outreach.py` | `tests/test_outreach.py` |
| 6.4 A6 — No longer needs goods | `record_outreach_outcome("no_longer_needed")` → `Timeout` with note (see decision 4) | `bam/services/outreach.py` | `tests/test_outreach.py` |

The HTTP surface for all of the above is exercised end-to-end in
`tests/test_api.py`.

## Interpretation decisions

The spec is ambiguous or contradictory in a few places. These are the calls
this implementation makes. **Decisions 1 and 4 diverge from (or resolve a
contradiction in) the spec text and should be reviewed by the BAM team.**

### 1. Intake rows are kept and scrubbed, not deleted — *question for the team*

Spec 6.1 step 6 says the system "deletes Intake Table row", but the spec's own
sequence diagram links the submission to the household (`LINK Form Submissions
field`), and the Households schema in section 4 has a `Form Submissions` link
field — both of which only make sense if the row survives. **We keep
`FormSubmission` rows linked to the household**, and the daily privacy scrub
clears their PII (name, phone, email, notes, address) once they are processed
and older than the retention window. This preserves an auditable intake
history without retaining raw PII. If the team really wants hard deletion,
that is a small change to `bam/services/privacy.py` — please confirm which
behavior is intended.

### 2. Request dedup

A household never gets a second `Open` request of the same type. Intake skips
the duplicate and reports it in the result (`skipped_duplicate_types`) rather
than failing, so a re-submission simply refreshes the household's contact
info.

### 3. Auto-expiration spares booked households

`Open` requests older than their window (14 days default, 30 for pots & pans,
14 for all social services, measured from `request_opened_at`) are set to
`Timeout` — **except** when the household currently has a `Booked`
appointment. Expiring someone who is already scheduled for a distro would
contradict the point of the queue.

### 4. "No longer needs goods" (A6): Timeout, not Delivered — *question for the team*

The spec contradicts itself: the outreach flowchart routes "yes but no longer
in need of goods/services" to the *"marked 'timeout'"* terminal, while the
6.4 table row A6 says *"mark complete"*. We follow the flowchart: the requests
are set to `Timeout` with a `[no longer needed]` note on the household. Marking
them `Delivered` would inflate fulfillment metrics with goods that were never
handed out. If the team prefers a distinct "withdrawn/complete" status, the
enum in `bam/models.py` is the place to add it — please confirm.

### 5. Missed appointments

`missed_appointment_count` increments on each no-show and resets to 0 when the
household checks in. At `BAM_MAX_MISSED_APPOINTMENTS` (default 2, per the
spec's "timeout after 2nd missed appointment"), all of the household's open
goods **and** social service requests time out.

### 6. Partial fulfillment (A1)

There is no partial-fulfillment state. If an item is out of stock, the
volunteer simply doesn't mark that request delivered; it stays `Open` and the
household keeps its place in the queue, exactly as the spec's design decision
in section 7 intends.

### 7. Wrong number (A5)

Sets `invalid_phone_number=True` on the household and times out all of its
open requests. The household is thereafter excluded from outreach lists and
blasts until a new intake submission arrives with a valid number.
