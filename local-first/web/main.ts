/**
 * Browser entry: device identity, org create/join, then boot the operator
 * console (copied verbatim from bam/web) on the CRDT-backed api adapter.
 *
 * Boot order matters: the console's app.js and views are classic IIFE
 * scripts expecting window.BAM; we set BAM.api (the adapter) and
 * BAM.LANGUAGES first, inject the scripts in order, register the extra
 * Roster view, then call BAM.start().
 */

import { WebCryptoSigner } from "@automerge/automerge-subduction";
import { IndexedDBStorageAdapter } from "@automerge/automerge-repo-storage-indexeddb";
import { openStore, type BamStore } from "../src/store.ts";
import { makeWebApi } from "../src/webapi.ts";
import { registerRosterView } from "../src/roster-view.ts";
import { LANGUAGES } from "../src/domain/catalog.ts";

interface AppConfig {
  mode: "create" | "join";
  orgName?: string;
  rosterUrl?: string;
  endpoint?: string;
  relayPeer?: string;
}

const CONFIG_KEY = "bam-local-first-config";

function loadConfig(): AppConfig | null {
  try {
    const raw = localStorage.getItem(CONFIG_KEY);
    return raw ? (JSON.parse(raw) as AppConfig) : null;
  } catch {
    return null;
  }
}

function saveConfig(config: AppConfig): void {
  localStorage.setItem(CONFIG_KEY, JSON.stringify(config));
}

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  attrs: Record<string, string> = {},
  text?: string
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text) node.textContent = text;
  return node;
}

/** First-run screen: create a new org or join one by roster URL. */
function firstRunScreen(root: HTMLElement, peerId: string): Promise<AppConfig> {
  return new Promise((resolve) => {
    root.innerHTML = "";
    const wrap = el("div", { class: "card stack", style: "max-width:560px;margin:40px auto;" });
    wrap.append(el("h2", { class: "card__title" }, "BAM local-first — set up this device"));
    const idNote = el("div", { class: "list-item__meta" });
    idNote.textContent = "This device's peer id (an admin enrolls it on the roster):";
    const idBox = el("div", { class: "mono", style: "word-break:break-all;font-size:13px" }, peerId);
    wrap.append(idNote, idBox);

    const orgName = el("input", { class: "input", placeholder: "Org name (e.g. BAM)" });
    const createBtn = el("button", { class: "btn btn-primary btn-block" }, "Create a new org on this device");

    const rosterUrl = el("input", { class: "input", placeholder: "automerge:… roster URL" });
    const endpoint = el("input", { class: "input", placeholder: "wss://relay… (Subduction endpoint)" });
    const relayPeer = el("input", { class: "input", placeholder: "relay peer id (64 hex — printed by the server)" });
    const joinBtn = el("button", { class: "btn btn-block" }, "Join an existing org");

    createBtn.onclick = () => {
      resolve({ mode: "create", orgName: orgName.value.trim() || "BAM" });
    };
    joinBtn.onclick = () => {
      if (!rosterUrl.value.trim().startsWith("automerge:")) {
        alertText("A roster URL starting with automerge: is required to join.");
        return;
      }
      resolve({
        mode: "join",
        rosterUrl: rosterUrl.value.trim(),
        endpoint: endpoint.value.trim() || undefined,
        relayPeer: relayPeer.value.trim() || undefined,
      });
    };

    const alertBox = el("div", { class: "list-item__meta", style: "color:var(--danger)" });
    function alertText(msg: string): void {
      alertBox.textContent = msg;
    }

    wrap.append(
      el("h3", { class: "card__title", style: "font-size:14px;margin-top:12px" }, "Create"),
      orgName,
      createBtn,
      el("h3", { class: "card__title", style: "font-size:14px;margin-top:12px" }, "Join"),
      rosterUrl,
      endpoint,
      relayPeer,
      joinBtn,
      alertBox
    );
    root.append(wrap);
  });
}

function injectScript(src: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = src;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error(`failed to load ${src}`));
    document.body.append(script);
  });
}

async function boot(): Promise<void> {
  const root = document.getElementById("boot-root")!;
  const signer = await WebCryptoSigner.setup();
  const peerId = signer.peerId().toString();

  let config = loadConfig();
  if (!config) {
    config = await firstRunScreen(root, peerId);
  }

  root.innerHTML = "<div class='loading'>Opening the local store…</div>";
  const storage = new IndexedDBStorageAdapter("bam-local-first");
  let store: BamStore;
  try {
    store = await openStore({
      signer,
      storage,
      endpoints: config.endpoint ? [config.endpoint] : [],
      alwaysAllow: config.relayPeer ? [config.relayPeer] : [],
      ...(config.mode === "join"
        ? { rosterUrl: config.rosterUrl }
        : { createOrg: config.orgName ?? "BAM", deviceName: "founding browser device" }),
    });
  } catch (err) {
    localStorage.removeItem(CONFIG_KEY);
    root.innerHTML = `<div class='card' style='max-width:560px;margin:40px auto'>
      <b>Could not open the org.</b>
      <div class='list-item__meta'>${err instanceof Error ? err.message : String(err)}</div>
      <div class='list-item__meta'>Config was reset — reload to try again.</div></div>`;
    return;
  }
  // Persist config only after a successful open, with the resolved roster URL
  // so subsequent loads work fully offline.
  saveConfig({ ...config, mode: "join", rosterUrl: store.roster.url });

  // Console bootstrap: adapter + languages first, then the classic scripts.
  const w = window as unknown as { BAM?: Record<string, unknown> };
  w.BAM = w.BAM || {};
  w.BAM.api = makeWebApi(store);
  w.BAM.LANGUAGES = [...LANGUAGES];

  await injectScript("/console/app.js");
  for (const view of ["checkin", "dashboard", "intake", "outreach", "distros", "admin"]) {
    await injectScript(`/console/views/${view}.js`);
  }
  registerRosterView(store);

  root.remove();
  (w.BAM as { start: () => void }).start();
}

void boot();
