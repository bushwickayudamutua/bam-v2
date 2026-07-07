# BAM local-first — the V2 system on an access-controlled CRDT

An experimental variant of the [BAM Mutual Aid System V2](../README.md) where
the data lives in **Automerge CRDT documents on each device** instead of a
central SQLite/Postgres database, synced through a
[Subduction](https://github.com/inkandswitch/subduction) relay with
**per-peer access control** — the Keyhive-track build of `automerge-repo`,
per Ink & Switch's guidance (`@automerge/automerge-repo@subduction`).

Volunteers' phones and laptops each hold a replica: check-ins at a distro
work with no connectivity, intake works offline, and everything merges when
devices sync. Access is controlled by an **org roster** of Ed25519 device
keys with admin/volunteer roles and revocation.

## Architecture

Two Automerge documents (see `src/schema.ts`):

- **Roster doc** — the access-control root: device peer ids (Ed25519
  verifying keys, hex) with roles, who added them, and revocations. It also
  carries the base doc's URL, so a new device joins with just the roster URL
  and a relay endpoint.
- **Base doc** — the operational data: the same six-table model as the
  server implementation (households, requests, social service requests,
  distros, fulfilled counts) keyed by stable ids, plus an **SMS outbox**.

Domain logic is ported 1:1 from the Python services (`src/domain/*.ts` ↔
`bam/services/*.py`): intake dedup + anonymized-household reconnection via
phone hash, check-in/fulfillment idempotency, outreach with the spec's
randomized `[REQUEST_URL]`, 14/30-day auto-expiration, and the PII scrub.
The request-type catalog (`src/catalog.json`) is exported verbatim from
`bam/request_types.py` — same 41 goods / 13 social services / 12 languages.

Divergences from the server variant, deliberately: there is no
form-submissions table (intake applies directly), and there is no in-process
SMS provider — a blast **queues messages into the shared `smsOutbox`**, and
any gateway device drains it (`bam-lf outbox`, `markOutboxSent`). That is
the local-first equivalent of the spec 5 `send_sms` function.

## The access-control model (read this before deploying)

Subduction authenticates every peer as an Ed25519 key and consults a
`Policy` before serving connects, fetches, and puts. This package's policy
(`src/roster.ts`) is **deny-by-default**: only peers with an active roster
entry are served. Facts below were verified empirically against
`automerge-repo@2.6.0-subduction.40` + a locally-built subduction server
(2026-07-06):

- **Compliant peers enforce the roster.** A device not on the roster is
  refused connects/fetches/puts by every BAM device, and revocation takes
  effect immediately (the policy reads the live roster doc).
- **The relay is a peer too.** Each device's policy gates its own dial to
  the relay, so the relay's peer id (printed at server startup) must be
  explicitly trusted (`--relay-peer`, or `relayPeer` in the web app).
- **An `--auth open` relay serves anyone who knows a doc URL.** Client-side
  policies protect peer-served traffic, not what a permissive relay has
  mirrored. **Do not put real PII through an open or third-party relay.**
- **`--auth keyhive` initializes the Keyhive access-control stack** on the
  server, but in today's build undelegated docs are still approved
  (maintainers: *"we just approve all for now"*; their end-to-end demo is
  still private). Server-side enforcement will tighten as Keyhive
  delegation ships end-to-end; until then, run your own relay in a trusted
  environment.
- **Revocation stops future syncs; it cannot recall data a device already
  replicated** (true of every distributed system).
- **No end-to-end encryption is claimed.** Blobs on the relay are not E2EE
  in this build.

## Quickstart

Requires **Node >= 22.13** (`Promise.withResolvers`).

```sh
cd local-first
npm install
npm test                 # 41 tests (unit + policy; e2e sync tests are relay-gated)
```

CLI walkthrough (offline):

```sh
alias bam-lf="node --experimental-strip-types src/cli.ts"
bam-lf identity                          # create/show this device's key
bam-lf org create --name "BAM"           # bootstrap: this device becomes admin
bam-lf roster list
bam-lf import --snapshot ../airtable-snapshot   # real data, stays local
bam-lf stats
```

### Syncing two devices through a relay

Build and run a Subduction server (Rust; not on crates.io yet):

```sh
git clone https://github.com/inkandswitch/subduction && cd subduction
cargo build --release -p subduction_cli
head -c 32 /dev/urandom | xxd -p -c 64 > relay.key   # stable identity
./target/release/subduction_cli server --socket 127.0.0.1:8944 \
  --data-dir /tmp/subduction-data --key-file relay.key --auth keyhive
# note the "Peer ID" line it prints — that's the relay key clients must trust
```

Device A (has the org): `bam-lf sync --endpoint ws://127.0.0.1:8944 --relay-peer <relay-hex>`

Device B (new): ask an admin to `bam-lf roster add --peer <B's id> --name "Vol phone"`,
then `bam-lf org join --roster <automerge:url> --endpoint ws://127.0.0.1:8944 --relay-peer <relay-hex>`.

**Relay trust.** Each device's policy gates its own dial, so the relay's
peer id must be trusted. Two ways to provide it:

- `--relay-peer <hex>` — pin a known key up front (preferred when the
  operator can distribute it, e.g. your own relay).
- `--trust-relay` — trust-on-first-use: connect without knowing the key,
  learn the relay's peer id from the handshake, and **pin it in
  `state.json`** so every later run verifies it. For relays whose key isn't
  published — like the maintainers' experiment relay. In the browser app,
  leaving the relay-peer field empty does the same. TOFU is only safe in
  this client-only topology (nothing can dial *us*), and only for orgs
  without real PII.

### The Ink & Switch experiment relay

The maintainers' relay, `wss://sync.subduction.inkandswitch.com`, is this
package's **default endpoint** (`DEFAULT_SYNC_ENDPOINT`) and per the
maintainers currently approves all relay traffic — fine for experiments,
**never for real PII** (an approve-all relay serves anyone who knows a doc
URL). As of 2026-07-06 the hostname is **not in public DNS** (NXDOMAIN from
Cloudflare and Google resolvers), so it isn't reachable yet — ask the
maintainers when it goes live. The moment it resolves, using it is:

```sh
bam-lf sync --trust-relay                       # default endpoint, learn + pin
bam-lf org join --roster <url> --trust-relay
```

(The TOFU path is verified end-to-end against a local relay in this repo:
dial with no pre-known key → learn → pin → the pinned key matches the
relay's actual key.)

The end-to-end path (B joins by roster URL and receives the org data) runs
in CI-skippable tests: `SUBDUCTION_RELAY=ws://127.0.0.1:8944
SUBDUCTION_RELAY_PEER=<hex> npm test`.

## Browser console

```sh
npm run dev     # http://localhost:5173
```

The **same operator console** as the server variant (the views are copied
verbatim from `bam/web/`) runs on a CRDT-backed `BAM.api` adapter
(`src/webapi.ts`) — check-in, intake, outreach, distros, dashboard, admin —
plus a **Roster** view for enrolling/revoking devices. First run: create an
org (or paste a roster URL + relay endpoint + relay peer id to join).
Device identity is a non-extractable WebCrypto key in IndexedDB; data
persists locally in IndexedDB and works fully offline.

## Known limitations

- Pre-release research software throughout (`2.6.0-subduction.40`); APIs
  will change.
- The in-process `subductionAdapters` MessageChannel bridge completes its
  handshake but registers no sync peers in this build, so tests use a real
  relay instead (see `test/sync.test.ts`).
- Single base doc: fine at BAM's scale (1.2k households / 8k requests
  imported in ~4s); shard per-year or per-table if it ever isn't.
- A hostile *modified* client on the roster can ignore local policy; that
  class of enforcement is exactly what Keyhive's convergent capabilities
  are for, once end-to-end delegation ships.

## Deploying a password-gated demo (single-file + StatiCrypt)

The app can be built as one self-contained `index.html` (JS + WASM inlined)
and encrypted with [StatiCrypt](https://github.com/robinmoisson/staticrypt)
so it can be hosted anywhere (e.g. GitHub Pages) behind a password:

```sh
npm run build:single                         # → dist-single/index.html (one file)
npx staticrypt dist-single/index.html -p <password> --short -d out
# deploy out/index.html (+ an empty .nojekyll) to any static host
```

Opened in a browser, it asks for the password, then the console runs. **Create
a new org** to test the full single-device experience offline (no server). To
test multi-device sync, **Join** with a roster URL + a `wss://` Subduction
relay endpoint + the relay's peer id (see the relay section above).

**Browser requirement:** device identity uses **Ed25519 WebCrypto**, which
needs a recent browser — Chrome/Edge ≳ 137, Safari 17+ (iOS 17+), Firefox.
Older browsers can't create the device key and the app won't boot.

The single-file build forces the `import` resolve condition
(`vite.config.ts`) so `@automerge/automerge-subduction` uses its
internally-consistent `web.js` glue; the default `browser` entry
(`bundler.js`) mixes glue instances and throws "expected instance of Topic"
on ephemeral subscribe.

## QR volunteer onboarding

Admins can onboard a volunteer with **one QR scan** — no peer-id exchange,
no manual roster step for each device:

1. Admin (Roster view → *QR invite*, or `bam-lf roster invite --name "July
   distro"`): mints an invite — a random secret whose **sha256 lives in the
   roster doc** with a role (always `volunteer`), an expiry (default 7
   days), a use cap (default 20), and revocability. The QR encodes the
   console URL + roster URL + relay endpoint + the secret.
2. Volunteer scans it → the console opens with a one-field screen ("You're
   invited to BAM — your name?") → tap **Join**.
3. The device mints its Ed25519 key, syncs the roster, and **self-enrolls**
   with the secret as proof. Every replica validates the enrollment
   (`sha256(proof) == invite.tokenHash`, within expiry, invite not revoked,
   role == the invite's role) — so a forged entry, a wrong secret, or a
   self-granted `admin` role is rejected by every compliant peer.

Security posture, plainly: **the QR is a bearer credential** (like a Signal
group link). Anyone who scans it before expiry/revocation joins as a
volunteer. Treat it like a key: short expiry, revoke after the onboarding
session (`roster revoke-invite`), and admin rights are never QR-grantable.
Verified end-to-end in real browsers: admin mints the QR in one profile, a
fresh profile opens the link, names itself, and appears on the admin's
roster as a volunteer — through a live relay, and through the
StatiCrypt-gated deployed demo (the `#invite=` fragment survives the
password gate).
