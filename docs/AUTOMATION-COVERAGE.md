# Automation coverage vs. `bam-automation@zakieh/automations`

Comparison of this implementation against every automation on the
[`zakieh/automations`](https://github.com/bushwickayudamutua/bam-automation/tree/zakieh/automations)
branch of `bam-automation` (the production V2 automation set), captured
2026-07-06.

**Short answer: no, we don't cover all of them.** We cover the core spec
flows (intake → requests, fulfillment counting, website JSON, SMS blast,
auto-expiration). The gaps are concentrated in the V2 Airtable automation
scripts (`automation-scripts/v2/`) and the daily maintenance crons.

Legend: ✅ covered · 🟡 partial / divergent-by-design · ❌ not covered

## V2 Airtable automation scripts (`automation-scripts/v2/`)

These are the new V2-specific automations and the most relevant set.

| Automation | What it does in prod | Status | Notes |
|---|---|---|---|
| `transform-form-submission/clean-record.js` | Calls the `/clean-record` API: phone + email validation **and** address geocoding → cleaned address, **address accuracy**, **BIN** (building id), **plus_code** | 🟡 | We do phone + email validation (`bam/validation.py`). We do **not** do address geocoding, BIN, or plus_code. `Request.geocode` exists but is only populated from an import and never computed — the whole Google Maps + NYC Planning Labs pipeline (in `bam-automation`'s `app/`) is unported. |
| `transform-form-submission/create-requests.js` | Creates `Requests` / `Social Service Requests` / **`Mesh Requests`** from the submission; splits furniture/kitchen; routes low-cost-internet to a **Mesh** record with BIN + cleaned address + accuracy; stamps `Last Requested` | 🟡 | `bam/services/intake.py` creates goods + social-service requests per type with furniture addresses and dedup. But it does **not** create a Mesh pipeline record from a live form — "internet" becomes a plain social-service request; `mesh_internet` rows only arrive via the importer. |
| `transform-form-submission/delete-submission.js` | Hard-deletes the form submission after processing | 🟡 | Deliberate divergence (SPEC-MAPPING decision 1): we **keep** submissions and scrub their PII on a retention timer instead of deleting. Note: this branch confirms prod really does delete. |
| `count-closed-requests.js` | For Delivered requests: increment `Fulfilled Request Count` bucketed by **Status Last Updated At** date, then **delete** the request. Mesh installs counted once per phone. | 🟡 | We increment `FulfilledRequestCount` on fulfillment (`checkin.fulfill_requests`) but keep the request (Delivered/Timeout) and scrub later — we never delete-after-count. We also don't do the mesh-install "one count per phone" dedup, nor bucket by Status-Last-Updated date. |
| `merge-households.js` | Merge duplicate households: union languages/requests/mesh links, min/max legacy + last-texted/called dates, pick latest appointment, concat notes, delete the losers | ❌ | **No household merge exists.** Our phone-keyed model prevents *new* duplicates at intake, but there is no tool to merge households that already diverged (e.g. two phone numbers, one family). |
| `merge-households/consolidate-requests.js` | Consolidate duplicate open requests of the same type per household: keep earliest, merge `Last Requested`/legacy dates, rank **mesh status**, pick the best **address bundle** by accuracy, dedup partner orgs / internet-access, delete dupes | 🟡 | Intake refuses a second `Open` request of the same type (prevents dupes at creation), but there is no standalone consolidation of existing duplicates, and none of the mesh-status-ranking / address-bundle / partner-org merge logic. |
| `delete-household.js` | Hard-delete a household | ❌ | We anonymize inactive households (keeping `phone_hash`) rather than hard-delete; there is no admin hard-delete action. |

## Scheduled crons (`functions/project.yml` + `cron/*`)

| Job | Schedule | Status | Notes |
|---|---|---|---|
| `UpdateWebsiteRequestData` | hourly (`3 * * * *`) | ✅ | `bam/services/metrics.py` + `bam website-data` / `POST /jobs/website-data`. |
| `DedupeAirtableViews` | daily 10:33pm ET | ❌ | Airtable-view phone dedup across ~23 views. Our key model avoids some of this, but there is no dedup job (and see `merge-households` above). |
| `UpdateMailjetLists` | daily | ❌ | Sync contacts to Mailjet email lists. **No email/Mailjet integration at all.** |
| `SnapshotAirtableViews` | daily | ❌ | Back up modified records to S3 (Digital Ocean Spaces). **No backup/snapshot job.** |

## Web-triggered / core functions (`core/bam_core/functions/`)

| Function | Status | Notes |
|---|---|---|
| `send_dialpad_sms` / `send_dialpad_sms_v2` | ✅ (differently) | We ship a pluggable SMS blast (`send_text_blast`, console/Twilio) with the spec's randomized `[REQUEST_URL]` and rate limiting; the local-first variant queues to a shared outbox. Provider is Twilio, not Dialpad. |
| `timeout_eg_requests` | 🟡 | We auto-expire `Open` requests on 14/30-day windows (`bam/services/expiration.py`). We do **not** implement the specific "time out an older request when a newer one of the same type is fulfilled" rule. |
| `consolidate_eg_requests` | 🟡 | See `consolidate-requests.js` above — not ported as a standalone job. |
| `analyze_fulfilled_requests` | ❌ | Snapshot-replay analytics over historical S3 snapshots. Not ported (we have no snapshot store). |
| `update_airtable_field_value` | n/a | Generic bulk field-update admin tool; not a domain automation. |
| `clean_airtable_views` / `dedupe_airtable_views` | ❌ | Airtable view maintenance; N/A to our relational/CRDT model except where it overlaps household/request dedup (not covered). |

## Summary of real gaps

Ordered by likely operational importance:

1. **Household merge** (`merge-households.js`) — no equivalent. The most
   substantive missing automation.
2. **Request consolidation** with mesh-status ranking + address-bundle
   selection (`consolidate-requests.js`) — only creation-time dedup exists.
3. **Address geocoding pipeline** (`clean-record` → cleaned address,
   accuracy, BIN, plus_code) — we validate phone/email only; `geocode` is
   inert.
4. **Live Mesh-request creation** at intake — internet requests don't enter
   the Mesh install pipeline; mesh only arrives via import.
5. **Fulfilled-count model** — prod counts-then-deletes closed requests
   (bucketed by status-change date, mesh deduped per phone); we count-and-keep.
6. **Daily maintenance crons** — Mailjet email sync, S3 snapshot/backup,
   and view dedup are all absent.
7. **Hard deletes** (`delete-household`, `delete-submission`) — we
   anonymize/keep by design; worth confirming with the team which behavior
   is wanted (SPEC-MAPPING decisions 1).

Points 3–7 line up with the earlier prod-code gap audit; points 1, 2, 4, 5
are sharpened by this branch, which is the first place the V2 automation
*scripts* (not just the Python functions) are visible.
