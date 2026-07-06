# Spec gaps — what the real production code does that the V2 spec (and this implementation) don't

Companion to [SPEC-MAPPING.md](SPEC-MAPPING.md). That doc maps the spec onto this
codebase and records interpretation decisions. **This doc goes the other
direction:** it audits the actual `bushwickayudamutua/bam-automation` production
code (both the legacy V1 tag-model and the live V2 normalized base) plus the
volunteer check-in guide, and lists behaviors the V2 spec never captured — so
they were never implemented here either.

Method: read the V2 automation (`core/bam_core/lib/airtable_v2.py`,
`send_dialpad_sms_v2.py`, `automation-scripts/v2/*`), the full functions layer
(`core/bam_core/functions/*`, `functions/project.yml`), the integrations/utils
(`dialpad`, `mailjet`, `google`/`nyc_planning_labs`/`olc`, `s3`, `phone`,
`email`, `geo`), the migration scripts, and the sibling repos (`baml.ink`,
`diaplad-automation`, `volunteer-checkin-guide`). The spec's own
`background-current-system.md` §3 actually lists most of these — the spec
*proper* (§5 "System Functions") then narrows to just two functions.

Legend: 🔴 whole subsystem missing · 🟠 behavior/field missing · 🟡 divergence to
reconfirm · ⚪ operational context.

---

## A. Whole subsystems the spec omits

### A1. 🔴 Email channel / Mailjet contact sync (`update_mailjet_lists`, daily)
Production syncs Airtable → Mailjet lists **Families**, **Volunteers**,
**All Contacts**, **Open Collective Donors** every day. One-way, additive
(never unsubscribes), skips any contact flagged with `Email Error`. Email is a
real backup outreach channel; the household even carries a `Needs Email
Outreach` flag (we import it but never act on it). The spec mentions email only
as a stakeholder integration.
- Missing here: no email model, no list-sync job, no email send path.

### A2. 🔴 Household deduplication / merge tooling (V2, implemented — NOT post-MVP)
The spec files household-merge under appendix A.7 "post-MVP," but the live V2
base **already runs it** as Airtable automations:
`merge-households.js` + `consolidate-requests.js` + `delete-household.js`.
Semantics worth copying exactly (they are easy to get wrong):
- Survivor = the first of the old household ids (keeps its record id/history);
  identity scalars (Name, Email, phone flags) come from the *new* record;
  Languages/Requests/Notes are pooled from **all** merged households; `Needs
  Delivery` / `Needs Email Outreach` are OR'd; the new record and the other
  olds are deleted.
- Post-merge request consolidation groups by request `Type`, keeps the
  **oldest** request (preserving `Request Opened At`) but adopts the **newest**
  `Last Requested` / `Geocode` / address values; `Internet Access` is unioned.
- Our intake only de-dups *within one household on re-submission*; there is no
  tool to merge two distinct households (e.g. same person, two phone numbers —
  the spec's own §7 edge case).

### A3. 🔴 Cross-record dedup job (`dedupe_airtable_views`, daily 10:33pm ET)
A daily pass over ~28 category views that, per phone number, **times out
all-but-the-oldest open request** (writes a per-view timeout tag). Special-cases
the MESH outreach view (an in-outreach record wins over a status-less one
regardless of date). In the normalized V2 model this maps to a scheduled
"shared-phone reconciliation" job. We have none — dedup here is intake-time only.

### A4. 🔴 Data backup / snapshots to S3 (`snapshot_airtable_views`, daily)
Daily incremental JSON snapshots of Assistance Requests, Volunteers, and
Essential Goods Donations to DigitalOcean Spaces, timestamped in ET.
**No retention policy — snapshots accumulate forever**, and they are the raw
material for the fulfillment analytics (A5). Spec appendix A.9 lists
backup/DR as post-MVP, but production runs it daily. We have no backup story.

### A5. 🔴 Snapshot-replay fulfillment analytics (`analyze_fulfilled_requests`, daily via GitHub Actions, midnight ET)
Replays the S3 snapshot history, detects each request that went
**open → delivered** between consecutive snapshots, and publishes: a Google
Sheet ("BAM Fulfilled and Open Requests") + a **public `fulfilled-requests.json`
on S3** (31-day rolling window, phone numbers SHA-256-hashed with a salt).
The public website consumes **both** `open-requests.json` *and*
`fulfilled-requests.json`. Our `update_website_request_data` equivalent only
produces the open-request counts — the fulfilled public feed is missing.

### A6. 🔴 Address geocoding pipeline (spec has a passive "Geocode" field only)
Real intake runs a multi-provider pipeline (in the FastAPI app / `utils/geo.py`,
invoked from intake via `api.baml.ink/clean-record`):
Google Places Autocomplete → Google Address Validation (USPS standardization) →
NYC Planning Labs (**BIN** lookup) → Google Geocoding (lat/lng) → **8-char Open
Location Code** (deliberately truncated to *obscure* exact location). It emits
`cleaned_address`, `bin`, an **accuracy tier** (No result / Building /
Apartment), `plus_code`, and lat/lng — and the accuracy tier gates BIN lookup and
drives furniture-delivery routing and migration address selection. Geocoding is
Bushwick-biased: 10-mile strict bounds around a fixed "Mayday" point, default
city "Brooklyn, NY", a hardcoded zip fix, and a default-BIN reject list.
- Here, `geocode` is a passive string we copy from Airtable on import — no
  geocoding, no BIN, no accuracy tier, no delivery routing.

### A7. 🔴 Volunteers as data + donations
A `Volunteers: Main` table exists (feeds the Mailjet Volunteers list); an
`Essential Goods Donations: Main` table and an Open Collective donor integration
exist. The spec treats volunteers purely as actors and never models volunteers,
donations, or donors. (May be legitimately out of MVP scope — but it's real and
undocumented.)

---

## B. Data-model fields/tables the spec's schema misses

- 🟠 **Mesh Requests is a distinct table keyed by (phone, BIN)** — a household can
  have multiple mesh-internet requests, one per building, each with
  `Has LOS?`, `Roof Accessible?`, `Building Identification Number`,
  `Address Accuracy`. We fold mesh into `SocialServiceRequest`, which loses the
  per-building keying. (Also note: V2 intake writes low-cost-internet data into
  *both* Social Service Requests and Mesh Requests — a real dual-write.)
- 🟠 **Household ops fields**: `Last Called`, `Needs Delivery`,
  `Needs Email Outreach`, `Other Languages`, `Legacy First/Last Date Submitted`.
  (We carry most of these on import already — but they are not in the spec's §4
  schema, so their intended *semantics* are undocumented.)
- 🟠 **Request delivery fields for furniture**: address accuracy tier + BIN +
  geocode attached **only to furniture requests** at intake (furniture = needs
  delivery). We attach nothing.
- ✅ `Status Last Updated At` — the spec omits it, but this implementation
  *does* have `status_last_updated_at`. Not a gap here.

---

## C. Behavioral details the spec flattens

### C1. 🟠 Check-in flow (from the volunteer-checkin-guide — the canonical UX)
The real check-in flow differs from spec §6.3 in three concrete ways:
- **Look up by the last 4 digits of the phone number** (Ctrl/Cmd-F over a
  "Today's Appointments" view), not a full-phone lookup. We look up by full
  normalized phone + name search.
- Default landing is a **"Today's Appointments"** view (recipients booked for
  today). We have distros/appointments but no today-filtered check-in landing.
- **Per-item Timeout at check-in**: for each open request the volunteer asks
  "do you still need this?" → **NO ⇒ mark that item Timeout on the spot**;
  YES + in stock ⇒ Delivered; YES + out of stock ⇒ leave Open. Our
  `fulfill_requests` can only mark Delivered — there is no way to time out a
  single declined item at check-in (the spec only allows that decline path
  during *outreach*, as A6). "Checked-in" is set at the **end**, after all items
  are processed.

### C2. 🟠 SMS send mechanics (Dialpad)
- Rate limiting is load-bearing: **sleep 30s every 30 messages + 2s per
  recipient**; messages > **160 chars** are greedy word-split into multiple
  separate API sends; `dry_run` defaults to **True**; there is an
  **`exclude_households_view`** parameter to suppress specific recipients.
- `[FIRST_NAME]` is the **first word** of the name in V1 but the **whole
  `Household.name`** in `send_sms_v2` — an inconsistency to pin down.
- `[REQUEST_URL]` randomization (unique 4-hex suffix per message, to dodge
  Dialpad link-blocking) — ✅ we already implement this.
- `last_texted` is stamped even when the Dialpad send errors (a quirk to decide
  on).

### C3. 🟠 Outreach language routing (Twilio `send_mass_text.py`)
Language selection is richer than equality-matching: Quechua → Spanish,
Mandarin → Cantonese, English only if it's the *sole* language, otherwise send
an "All" message concatenating Spanish + Cantonese + English. Our outreach
filters by language string; it doesn't do fallback routing or the concatenated
multi-language message. (Also: a **second, entirely separate SMS stack** —
Twilio — coexists with Dialpad, with different phone formatting, rate limits,
and error-code handling.)

### C4. 🟠 "Time out older unfulfilled when a newer one is delivered" (`timeout_eg_requests`)
When a household has a delivered request of an item, its *earlier* still-open
requests for the same item auto-time-out. In the normalized model our intake
de-dup prevents a second open of the same type within a household, so this is
mostly covered — **except across a household merge** (A2), where pooled
duplicates need this rule.

### C5. 🟠 Phone & email normalization precision
- Phone: `phonenumbers` (US region). *Invalid* = fails `is_valid_number`;
  *international* = valid AND country code ≠ 1; format NATIONAL for US else
  INTERNATIONAL. Pre-cleaning strips Spanish annotations (`#invalido`,
  `#sin servicio`) and truncates at `alternativ…`; 7-char minimum. **Invalid
  phones are dropped entirely at migration ingest.** Dialpad *also* has its own
  separate phone cleaner (10→+1, 11→+, else raw).
- Email: ~40 null sentinels (incl. Spanish "no tengo", `na@na.com`, single
  letters), ~50 domain-misspelling fixes, TLD typo repair, missing-`@`/`.com`
  repair. Optional DNS deliverability check.
- Worth documenting as the normalization contract; our `validation.py` is much
  thinner.

---

## D. Divergences to reconfirm with the team (this impl departs from live V2)

- 🟡 **Form submission retention.** The live V2 base **deletes** the form
  submission after the transform runs (`delete-submission.js`; the ORM docstring
  says "one row per submit, deleted after Write automation") — matching the spec
  text's literal "deletes Intake Table row." **This implementation deliberately
  keeps + scrubs** `FormSubmission` rows (SPEC-MAPPING decision 1). Our choice is
  defensible (auditable history without raw PII) but it *diverges from production*
  — confirm which is wanted.
- 🟡 **Fulfillment counting is destructive in production.** `count-closed-requests`
  counts only `Delivered` requests into `Fulfilled Request Count` (keyed on
  `Status Last Updated At` date) and then **deletes every request in the batch,
  regardless of status** (non-delivered closed requests are purged uncounted).
  We keep requests and increment counts (auditable). Confirm whether closed
  requests should actually be purged.
- 🟡 **Mesh** folded into `SocialServiceRequest` vs. its own (phone, BIN)-keyed
  table (see B).

---

## E. Sibling-repo operational context (not in spec)

- ⚪ **`baml.ink`** — short-link + QR server. The `request` short link and the
  `fulfilled-requests` Google Sheet links live here; `[REQUEST_URL]` and the
  public data feeds tie into it.
- ⚪ **`diaplad-automation`** — a standalone CSV → Dialpad bulk-messaging web app
  (dialpad.baml.ink). A manual outreach path parallel to the automated
  `send_sms`; worth knowing it exists so the two aren't confused.
- ⚪ **`volunteer-checkin-guide`** — the canonical check-in UX documented in C1.
- ⚪ **`bushwickayudamutua.github.io`** — the public site that consumes
  `open-requests.json` (and, per A5, `fulfilled-requests.json`).

---

## Suggested priority

| P | Item | Why |
|---|---|---|
| P0 | C1 per-item Timeout at check-in | Volunteers do this every distro; we can't record it |
| P0 | D1 form-submission delete vs keep | We knowingly diverge from production — get a ruling |
| P1 | A2 household merge/consolidate | Implemented in live V2; our §7 edge case is unhandled |
| P1 | A5 public fulfilled-requests feed | Website already expects it; we only publish open counts |
| P1 | A1 email channel / Mailjet sync | Real second outreach channel; `Needs Email Outreach` is dead data without it |
| P2 | A6 geocoding + BIN + accuracy | Needed for furniture delivery routing; `geocode` is inert today |
| P2 | A4 backup/snapshots | No DR story; also unblocks A5-style analytics |
| P2 | C2/C3 SMS pacing + language routing | Correctness of real sends (rate limits, multi-language) |
| P3 | A3 shared-phone reconciliation job | Scheduled hygiene; intake dedup covers the common case |
| P3 | A7 volunteers/donations | Likely out of MVP scope, but undocumented |
