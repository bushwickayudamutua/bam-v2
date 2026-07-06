/**
 * "Roster" console view — the access-control panel this branch exists for.
 *
 * Shows this device's identity and sync status, and (for admins) manages
 * the roster: add a device by its peer id, revoke one. Registered into the
 * console's view registry after app.js loads, so it uses the same BAM.h /
 * component classes as every other view.
 */

import type { BamStore } from "./store.ts";
import { addMember, revokeMember, isAdmin } from "./roster.ts";
import type { Role } from "./schema.ts";

interface BamNamespace {
  h: (tag: string, attrs?: unknown, ...children: unknown[]) => HTMLElement;
  clear: (el: HTMLElement) => void;
  toast: (msg: string, kind?: string) => void;
  fmtDateTime: (iso: string) => string;
  registerView: (name: string, def: { title: string; icon?: string; render: (c: HTMLElement) => void }) => void;
}

export function registerRosterView(store: BamStore): void {
  const BAM = (window as unknown as { BAM: BamNamespace }).BAM;
  const { h, clear, toast } = BAM;

  function render(container: HTMLElement): void {
    const roster = store.roster.doc()!;
    const admin = isAdmin(roster, store.peerId);

    clear(container);

    const heading = h(
      "div",
      { class: "view-heading" },
      h("h1", {}, "Roster"),
      h(
        "p",
        { class: "muted" },
        "Which devices may sync this org's data. Deny-by-default: a device not on this roster is refused by every compliant peer."
      )
    );

    // This device.
    const me = h(
      "div",
      { class: "card stack" },
      h("h2", { class: "card__title" }, `${roster.org} — this device`),
      h("div", { class: "list-item__meta" }, "Peer id (share with an admin to be enrolled):"),
      h("div", { class: "mono", style: { wordBreak: "break-all", fontSize: "13px" } }, store.peerId),
      h(
        "div",
        { class: "row" },
        h(
          "button",
          {
            class: "btn",
            onclick: () => {
              void navigator.clipboard.writeText(store.peerId).then(() => toast("Peer id copied.", "success"));
            },
          },
          "Copy peer id"
        ),
        h(
          "span",
          { class: "pill" },
          store.repo.isSubductionConnected() ? "sync: connected" : "sync: offline"
        ),
        h("span", { class: "pill" }, admin ? "admin" : "volunteer")
      ),
      h("div", { class: "list-item__meta" }, "Roster URL (new devices join with this):"),
      h("div", { class: "mono", style: { wordBreak: "break-all", fontSize: "13px" } }, store.roster.url)
    );

    // Members list.
    const members = Object.values(roster.members).sort((a, b) =>
      a.addedAt < b.addedAt ? -1 : 1
    );
    const memberRows = members.map((m) =>
      h(
        "li",
        { class: "list-item" },
        h(
          "div",
          { class: "list-item__body" },
          h("div", { class: "list-item__label" }, m.name),
          h(
            "div",
            { class: "list-item__meta mono", style: { wordBreak: "break-all" } },
            m.peerId
          )
        ),
        h(
          "span",
          { class: `badge ${m.revokedAt ? "badge-timeout" : "badge-open"}` },
          m.revokedAt ? "revoked" : m.role
        ),
        admin && !m.revokedAt && m.peerId !== store.peerId
          ? h(
              "button",
              {
                class: "btn btn-danger",
                onclick: () => {
                  try {
                    revokeMember(store.roster, store.peerId, m.peerId);
                    toast(`Revoked ${m.name}.`, "success");
                    render(container);
                  } catch (err) {
                    toast(err instanceof Error ? err.message : String(err), "error");
                  }
                },
              },
              "Revoke"
            )
          : null
      )
    );
    const membersCard = h(
      "div",
      { class: "card stack" },
      h("h2", { class: "card__title" }, `Members (${members.length})`),
      h("ul", { class: "list" }, memberRows)
    );

    // Admin: add a device.
    const peerInput = h("input", {
      class: "input",
      id: "roster-peer",
      placeholder: "peer id (64 hex chars)",
    }) as HTMLInputElement;
    const nameInput = h("input", {
      class: "input",
      id: "roster-name",
      placeholder: "device / volunteer name",
    }) as HTMLInputElement;
    const roleSelect = h("select", { class: "input", id: "roster-role" }) as HTMLSelectElement;
    for (const role of ["volunteer", "admin"]) {
      const opt = document.createElement("option");
      opt.value = role;
      opt.textContent = role;
      roleSelect.append(opt);
    }
    const addCard = admin
      ? h(
          "div",
          { class: "card stack" },
          h("h2", { class: "card__title" }, "Enroll a device"),
          h("div", { class: "field" }, h("label", { class: "label", for: "roster-peer" }, "Peer id"), peerInput),
          h("div", { class: "field" }, h("label", { class: "label", for: "roster-name" }, "Name"), nameInput),
          h("div", { class: "field" }, h("label", { class: "label", for: "roster-role" }, "Role"), roleSelect),
          h(
            "button",
            {
              class: "btn btn-primary btn-block",
              onclick: () => {
                const peerId = peerInput.value.trim().toLowerCase();
                const name = nameInput.value.trim();
                if (!/^[0-9a-f]{64}$/.test(peerId)) {
                  toast("Peer id must be 64 hex characters.", "error");
                  return;
                }
                if (!name) {
                  toast("Give the device a name.", "error");
                  return;
                }
                try {
                  addMember(store.roster, store.peerId, {
                    peerId,
                    name,
                    role: roleSelect.value as Role,
                  });
                  toast(`Enrolled ${name}.`, "success");
                  render(container);
                } catch (err) {
                  toast(err instanceof Error ? err.message : String(err), "error");
                }
              },
            },
            "Enroll device"
          )
        )
      : h(
          "div",
          { class: "card" },
          h("div", { class: "empty-state" }, h("div", {}, "Only admins can enroll or revoke devices."))
        );

    container.append(heading, me, membersCard, addCard);
  }

  BAM.registerView("roster", { title: "Roster", icon: "🔑", render });
}
