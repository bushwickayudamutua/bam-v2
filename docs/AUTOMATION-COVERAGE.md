# Automation coverage vs. `bam-automation@zakieh/automations`

Comparison of this implementation against every automation on the
[`zakieh/automations`](https://github.com/bushwickayudamutua/bam-automation/tree/zakieh/automations)
branch of `bam-automation` (the production V2 automation set), captured
2026-07-06.

**Status: at parity.** Every automation on the branch now has an
equivalent. The externally-dependent ones (address geocoding, Mailjet, S3)
are implemented as pluggable integrations — real when their credentials are
configured, a safe passthrough/dry-run otherwise — the same pattern as the
SMS layer. See "Parity build" at the bottom for the modules and how to run
them.

Legend: ✅ covered · 🟡 covered as pluggable / opt-in · (was ❌/🟡 before
the parity build — see the note in each row)

## V2 Airtable automation scripts (`automation-scripts/v2/`)

These are the new V2-specific automations and the most relevant set.

| Automation | What it does in prod | Status | Coverage |
|---|---|---|---|
| `transform-form-submission/clean-record.js` | Calls the `/clean-record` API: phone + email validation **and** address geocoding → cleaned address, **address accuracy**, **BIN** (building id), **plus_code** | ✅ | Phone/email in `bam/validation.py`; the address pipeline is `bam/services/geocoding.py` (`GoogleNycGeocoder`: Google Maps geocode + accuracy + plus_code, NYC Planning Labs GeoSearch for BIN). Intake now populates `geocode`/`bin`/`address_accuracy` on furniture + mesh requests. Passthrough `NoopGeocoder` when no Google key. *(was 🟡)* |
| `transform-form-submission/create-requests.js` | Creates `Requests` / `Social Service Requests` / **`Mesh Requests`** from the submission; splits furniture/kitchen; routes low-cost-internet to a **Mesh** record with BIN + cleaned address + accuracy; stamps `Last Requested` | ✅ | Intake routes low-cost internet to a `mesh_internet` request with `mesh_status="Open"` + geocoded BIN/address/accuracy (`MESH_KEYS`). *(was 🟡)* |
| `transform-form-submission/delete-submission.js` | Hard-deletes the form submission after processing | ✅ | `bam/services/admin.delete_submission` + `DELETE /submissions/{id}`. (Default lifecycle still keeps+scrubs — decision 1 — but the hard delete now exists.) *(was 🟡)* |
| `count-closed-requests.js` | For Delivered requests: increment `Fulfilled Request Count` bucketed by **Status Last Updated At** date, then **delete** the request. Mesh installs counted once per phone. | ✅ | `bam/services/count_closed.py` (`bam count-closed [--delete]`): buckets by status-change date, dedups mesh installs per phone, deletes when enabled (`BAM_DELETE_AFTER_COUNT`). *(was 🟡)* |
| `merge-households.js` | Merge duplicate households: union languages/requests/mesh links, min/max legacy + last-texted/called dates, pick latest appointment, concat notes, delete the losers | ✅ | `bam/services/merge.py` + `POST /households/merge`: relinks requests, unions languages, max/min date rollups, latest appointment, concat notes, OR flags, deletes losers. *(was ❌)* |
| `merge-households/consolidate-requests.js` | Consolidate duplicate open requests of the same type per household: keep earliest, merge `Last Requested`/legacy dates, rank **mesh status**, pick the best **address bundle** by accuracy, dedup partner orgs / internet-access, delete dupes | ✅ | `bam/services/consolidate.py` + `POST /requests/consolidate`: keeps earliest, mesh grouped by BIN with `MESH_STATUS_RANK` + `ADDRESS_ACCURACY_RANK` bundle selection + unioned internet-access. *(was 🟡)* |
| `delete-household.js` | Hard-delete a household | ✅ | `bam/services/admin.delete_household` + `DELETE /households/{id}` (removes its requests too). *(was ❌)* |

## Scheduled crons (`functions/project.yml` + `cron/*`)

| Job | Schedule | Status | Coverage |
|---|---|---|---|
| `UpdateWebsiteRequestData` | hourly (`3 * * * *`) | ✅ | `bam/services/metrics.py` + `bam website-data` / `POST /jobs/website-data`. |
| `DedupeAirtableViews` | daily 10:33pm ET | ✅ | `bam/services/dedupe.py` + `bam dedupe-households` / `POST /jobs/dedupe-households`: merges same-phone-hash households and consolidates their requests. *(was ❌)* |
| `UpdateMailjetLists` | daily | ✅ | `bam/services/mailjet.py` + `bam mailjet-sync`: syncs valid-email contacts to a Mailjet list when `BAM_MAILJET_*` set, dry-run otherwise. *(was ❌)* |
| `SnapshotAirtableViews` | daily | ✅ | `bam/services/snapshot.py` + `bam snapshot`: full JSON snapshot to S3 (`BAM_S3_*`, needs `boto3`) or a local directory. *(was ❌)* |

## Web-triggered / core functions (`core/bam_core/functions/`)

| Function | Status | Coverage |
|---|---|---|
| `send_dialpad_sms` / `send_dialpad_sms_v2` | ✅ | Pluggable SMS blast (`send_text_blast`, console/Twilio) with the spec's randomized `[REQUEST_URL]` and rate limiting; the local-first variant queues to a shared outbox. Provider is Twilio, not Dialpad. |
| `timeout_eg_requests` | ✅ | Auto-expiration (`bam/services/expiration.py`, 14/30-day windows). The "older-of-same-type" case is subsumed by consolidation, which keeps one request per (household, type). |
| `consolidate_eg_requests` | ✅ | `bam/services/consolidate.py` (see above). |
| `analyze_fulfilled_requests` | ✅ | `bam/services/analytics.py` + `bam analyze` / `GET /metrics/analytics`: fulfilled-by-type/date + open backlog. Reads live data + the fulfilled-count history rather than replaying S3 snapshots. |
| `update_airtable_field_value` | n/a | Generic bulk field-update admin tool; not a domain automation. |
| `clean_airtable_views` / `dedupe_airtable_views` | ✅ | Covered by `dedupe_households` where it overlaps our model (Airtable-view plumbing is N/A to a relational store). |

## Parity build

Everything above is exercised by `tests/test_automations.py` (+ API smoke in
`tests/test_api.py`) and was run against the full migrated dataset (1,252
households / 2,382 social requests, 1,478 of them mesh).

New modules: `bam/services/{merge,consolidate,dedupe,count_closed,geocoding,
mailjet,snapshot,analytics,admin}.py`. New model fields `Request.bin/
address_accuracy` and `SocialServiceRequest.mesh_status/bin/address_accuracy/
geocode`; existing databases upgrade automatically (`bam/db.py`
`_add_missing_columns` runs `ALTER TABLE ADD COLUMN` on `init_db`).

CLI: `bam consolidate [--household-id] · dedupe-households · count-closed
[--delete] · mailjet-sync · snapshot · analyze`. HTTP: `POST /households/
merge · /requests/consolidate · /jobs/{dedupe-households,count-closed,
mailjet-sync,snapshot}`, `GET /metrics/analytics`, `DELETE /households/{id}`,
`DELETE /submissions/{id}`.

Configuration for the pluggable integrations (all optional; passthrough/dry
without them): `BAM_GOOGLE_MAPS_API_KEY` (geocoding), `BAM_MAILJET_API_KEY/
_SECRET/_LIST_ID` (Mailjet), `BAM_S3_*` + `boto3` or `BAM_SNAPSHOT_DIR`
(snapshots), `BAM_DELETE_AFTER_COUNT` (count-then-delete).

### Deliberate differences from prod (unchanged, by design)

- **Submission/household lifecycle default** stays anonymize-and-keep
  (SPEC-MAPPING decision 1); the hard-delete actions and the delete-after-
  count job exist but are opt-in.
- **Analytics** computes from live data + fulfilled-count history rather
  than replaying S3 snapshots (same outputs, no snapshot store required).
