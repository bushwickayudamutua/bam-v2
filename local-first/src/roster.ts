/**
 * Roster-driven access control for the Subduction sync engine.
 *
 * Subduction authenticates every peer as an Ed25519 key (its PeerId) and
 * consults a `Policy` before serving connects, fetches, and puts. This
 * module implements that policy from the roster document: **deny by
 * default**, allow only peers with an active (non-revoked) roster entry.
 *
 * Trust model, honestly stated:
 * - Every device enforces this policy against every peer it talks to, so an
 *   unauthorized key cannot pull or push data from/to any *compliant* peer.
 * - A relay you don't run (e.g. the Ink & Switch experiment server, which
 *   currently approves all relay) will happily store-and-forward for
 *   anyone; your data still only flows to peers YOUR devices authorize,
 *   because your devices check the roster before serving them. For
 *   server-side enforcement too, run your own subduction server with
 *   `--keyhive` and the same roster.
 * - Revocation stops future syncs; it cannot un-share what a device already
 *   replicated (the same is true of any distributed system).
 *
 * The roster is itself a synced Automerge doc, so membership changes are
 * offline-capable and merge like everything else. Only admins may mutate it
 * (enforced in code here; a hostile *modified* client is out of scope for
 * policy hooks and is what keyhive-style convergent capabilities will close
 * once end-to-end auth ships in the sync server).
 */

import type { DocHandle } from "@automerge/automerge-repo";
import type { RosterDoc, RosterMember, Role } from "./schema.ts";
import { nowIso } from "./schema.ts";

/** Structural type of @automerge/automerge-subduction's Policy interface. */
export interface SubductionPolicyLike {
  authorizeConnect(peerId: unknown): Promise<void>;
  authorizeFetch(peerId: unknown, sedimentreeId: unknown): Promise<void>;
  authorizePut(requestor: unknown, author: unknown, sedimentreeId: unknown): Promise<void>;
  filterAuthorizedFetch(peerId: unknown, ids: unknown[]): Promise<unknown[]>;
}

export class NotAuthorized extends Error {}

export function isActiveMember(roster: RosterDoc | undefined, peerId: string): boolean {
  if (!roster) return false;
  const member = roster.members[peerId];
  return !!member && !member.revokedAt;
}

export function isAdmin(roster: RosterDoc | undefined, peerId: string): boolean {
  if (!roster) return false;
  const member = roster.members[peerId];
  return !!member && !member.revokedAt && member.role === "admin";
}

export interface RosterPolicyOptions {
  /**
   * Peers always allowed regardless of roster state — the relay server's
   * key(s), and this device's own key (a repo consults the policy for its
   * own operations too in some paths). Deny-by-default applies to everyone
   * else.
   */
  alwaysAllow?: string[];
  /**
   * Trust-on-first-use mode: allow ANY peer. Only sane in a client-only
   * topology (browser/CLI dialing out via `subductionWebsocketEndpoints`),
   * where the only peers that can appear are relays this device chose to
   * dial — there is no listener for strangers to reach. Used to learn a
   * relay's peer id on the first connect (e.g. the Ink & Switch experiment
   * relay, whose key isn't published); callers should capture the learned
   * id and switch back to pinned mode. Never combine with real PII.
   */
  trustAll?: boolean;
}

/**
 * Build a Subduction Policy from a live view of the roster.
 *
 * `getRoster` is called on every authorization decision so revocations take
 * effect as soon as the roster doc changes — pass a closure over the roster
 * DocHandle, not a snapshot.
 */
export function rosterPolicy(
  getRoster: () => RosterDoc | undefined,
  opts: RosterPolicyOptions = {}
): SubductionPolicyLike {
  const always = new Set(opts.alwaysAllow ?? []);

  const allowed = (peerId: unknown): boolean => {
    if (opts.trustAll) return true;
    const id = String(peerId);
    return always.has(id) || isActiveMember(getRoster(), id);
  };
  const assertAllowed = (peerId: unknown, what: string): void => {
    if (!allowed(peerId)) {
      throw new NotAuthorized(`peer ${String(peerId)} is not on the roster (${what})`);
    }
  };

  return {
    async authorizeConnect(peerId) {
      assertAllowed(peerId, "connect");
    },
    async authorizeFetch(peerId, _sedimentreeId) {
      assertAllowed(peerId, "fetch");
    },
    async authorizePut(requestor, _author, _sedimentreeId) {
      // The requestor must be authorized; the original author may be a
      // since-revoked member whose historical commits are still valid.
      assertAllowed(requestor, "put");
    },
    async filterAuthorizedFetch(peerId, ids) {
      return allowed(peerId) ? ids : [];
    },
  };
}

/** Admin action: add (or re-activate) a member. Throws unless `actor` is an
 * admin — or the roster is empty, in which case the first member bootstraps
 * as admin. */
export function addMember(
  handle: DocHandle<RosterDoc>,
  actor: string,
  member: { peerId: string; name: string; role: Role },
  now: string = nowIso()
): void {
  const doc = handle.doc();
  const bootstrap = Object.keys(doc?.members ?? {}).length === 0;
  if (!bootstrap && !isAdmin(doc, actor)) {
    throw new NotAuthorized(`peer ${actor} is not an active admin`);
  }
  if (bootstrap && member.peerId !== actor) {
    throw new NotAuthorized("the first roster member must be the acting device (bootstrap admin)");
  }
  handle.change((d) => {
    const existing = d.members[member.peerId];
    if (existing) {
      existing.name = member.name;
      existing.role = bootstrap ? "admin" : member.role;
      delete existing.revokedAt;
      delete existing.revokedBy;
      return;
    }
    const entry: RosterMember = {
      peerId: member.peerId,
      name: member.name,
      role: bootstrap ? "admin" : member.role,
      addedBy: actor,
      addedAt: now,
    };
    d.members[member.peerId] = entry;
  });
}

/** Admin action: revoke a member's access (future syncs are denied). */
export function revokeMember(
  handle: DocHandle<RosterDoc>,
  actor: string,
  peerId: string,
  now: string = nowIso()
): void {
  if (!isAdmin(handle.doc(), actor)) {
    throw new NotAuthorized(`peer ${actor} is not an active admin`);
  }
  if (peerId === actor) {
    throw new NotAuthorized("an admin cannot revoke itself (avoid lockout)");
  }
  handle.change((d) => {
    const member = d.members[peerId];
    if (!member) throw new Error(`no roster member ${peerId}`);
    member.revokedAt = now;
    member.revokedBy = actor;
  });
}
