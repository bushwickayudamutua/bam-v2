/**
 * Repo construction: identity, storage, roster-driven policy, and sync.
 *
 * `openStore` wires the pieces validated against
 * @automerge/automerge-repo@2.6.0-subduction.40:
 *
 *   new Repo({ signer, storage, subductionPolicy, subductionWebsocketEndpoints })
 *
 * The signer is the device identity (Ed25519). In the browser use
 * `WebCryptoSigner.setup()` (non-extractable key in IndexedDB); in Node the
 * CLI persists a MemorySigner's 32 secret bytes on disk (0600).
 *
 * Bootstrap order matters: the policy needs the roster, but the roster doc
 * itself arrives over sync. `openStore` therefore resolves the roster handle
 * first (create locally when new; find via URL when joining) and hands the
 * policy a live getter, so authorization always reflects the latest merged
 * roster state.
 */

import { Repo, initSubduction } from "@automerge/automerge-repo";
import type { DocHandle, StorageAdapterInterface } from "@automerge/automerge-repo";
import { emptyBamDoc, emptyRosterDoc, nowIso } from "./schema.ts";
import type { BamDoc, RosterDoc } from "./schema.ts";
import { addMember, rosterPolicy } from "./roster.ts";

/** Matches the Signer interface of @automerge/automerge-subduction. */
export interface SignerLike {
  sign(message: Uint8Array): Uint8Array | Promise<Uint8Array>;
  verifyingKey(): Uint8Array;
  peerId(): { toString(): string };
}

export const DEFAULT_SYNC_ENDPOINT = "wss://sync.subduction.inkandswitch.com";

export interface OpenStoreOptions {
  signer: SignerLike;
  storage?: StorageAdapterInterface;
  /** Subduction websocket endpoints; [] disables networking (tests, offline). */
  endpoints?: string[];
  /** Join an existing org: the roster doc's automerge URL. */
  rosterUrl?: string;
  /** Create a new org with this name (mutually exclusive with rosterUrl). */
  createOrg?: string;
  /** Display name for this device when bootstrapping a new org. */
  deviceName?: string;
  /** Extra peer ids the policy always allows (e.g. a relay's key). */
  alwaysAllow?: string[];
}

export interface BamStore {
  repo: Repo;
  peerId: string;
  roster: DocHandle<RosterDoc>;
  base: DocHandle<BamDoc>;
}

export async function openStore(opts: OpenStoreOptions): Promise<BamStore> {
  await initSubduction();
  const peerId = opts.signer.peerId().toString();

  // The policy reads the roster through this box so it is live from the
  // moment the handle resolves, while the Repo can be constructed first.
  const box: { roster?: DocHandle<RosterDoc> } = {};
  const policy = rosterPolicy(() => box.roster?.doc(), {
    alwaysAllow: [peerId, ...(opts.alwaysAllow ?? [])],
  });

  const repo = new Repo({
    signer: opts.signer as never,
    storage: opts.storage,
    subductionPolicy: policy as never,
    subductionWebsocketEndpoints: opts.endpoints ?? [],
  });

  let roster: DocHandle<RosterDoc>;
  let base: DocHandle<BamDoc>;
  const now = nowIso();

  if (opts.rosterUrl) {
    roster = await repo.find<RosterDoc>(opts.rosterUrl as never);
    box.roster = roster;
    const baseUrl = roster.doc()?.baseDocUrl;
    if (!baseUrl) throw new Error("roster has no baseDocUrl (org not fully initialized)");
    base = await repo.find<BamDoc>(baseUrl as never);
  } else {
    const org = opts.createOrg ?? "BAM";
    roster = repo.create<RosterDoc>(emptyRosterDoc(org, now));
    box.roster = roster;
    base = repo.create<BamDoc>(emptyBamDoc(org, now));
    roster.change((d) => {
      d.baseDocUrl = base.url;
    });
    // Bootstrap: the creating device becomes the first admin.
    addMember(roster, peerId, {
      peerId,
      name: opts.deviceName ?? "founding device",
      role: "admin",
    }, now);
  }

  return { repo, peerId, roster, base };
}
