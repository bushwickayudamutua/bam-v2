/* BAM micro-framework — the shell every view depends on.
 * Provides window.BAM: registerView, h, clear, toast, navigate, fmtDate,
 * fmtDateTime, start (hash router + nav builder).
 *
 * Namespace init is idempotent so this file and api.js may load in any order. */

(function () {
  "use strict";

  const BAM = (window.BAM = window.BAM || {});

  // View registry, in registration order (drives the nav).
  const views = new Map();

  /**
   * Register a view. Called by each view file at load time.
   * @param {string} name  route key (also the location.hash)
   * @param {{title:string, icon?:string, render:(container:HTMLElement, params:object)=>void}} def
   */
  BAM.registerView = function registerView(name, def) {
    views.set(name, Object.assign({ name }, def));
  };

  BAM.getViews = function getViews() {
    return Array.from(views.values());
  };

  /* DOM helper ------------------------------------------------------------ */

  const BOOLEAN_ATTRS = new Set([
    "disabled",
    "checked",
    "readonly",
    "required",
    "selected",
    "hidden",
    "autofocus",
  ]);

  function appendChild(el, child) {
    if (child === null || child === undefined || child === false || child === true) {
      return;
    }
    if (Array.isArray(child)) {
      child.forEach((c) => appendChild(el, c));
      return;
    }
    if (child instanceof Node) {
      el.appendChild(child);
      return;
    }
    el.appendChild(document.createTextNode(String(child)));
  }

  /**
   * Create an element. h("button", {class:"btn", onclick}, "Go")
   * attrs: class, id, event handlers (on*), dataset via data-*, style object,
   * boolean attrs (disabled...), everything else as an attribute.
   */
  BAM.h = function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    if (attrs) {
      for (const [key, value] of Object.entries(attrs)) {
        if (value === null || value === undefined || value === false) continue;
        if (key === "class" || key === "className") {
          el.className = value;
        } else if (key === "style" && typeof value === "object") {
          Object.assign(el.style, value);
        } else if (key === "dataset" && typeof value === "object") {
          Object.assign(el.dataset, value);
        } else if (key === "html") {
          el.innerHTML = value; // trusted, view-controlled content only
        } else if (key.startsWith("on") && typeof value === "function") {
          el.addEventListener(key.slice(2).toLowerCase(), value);
        } else if (BOOLEAN_ATTRS.has(key)) {
          if (value) el.setAttribute(key, "");
        } else {
          el.setAttribute(key, value);
        }
      }
    }
    children.forEach((c) => appendChild(el, c));
    return el;
  };

  /** Remove all children of an element. */
  BAM.clear = function clear(el) {
    while (el.firstChild) el.removeChild(el.firstChild);
    return el;
  };

  /* Toasts ---------------------------------------------------------------- */

  function toastHost() {
    let host = document.getElementById("toast-host");
    if (!host) {
      host = BAM.h("div", { id: "toast-host", class: "toast-host", "aria-live": "polite" });
      document.body.appendChild(host);
    }
    return host;
  }

  /** Transient notification. kind: "info" | "success" | "error". */
  BAM.toast = function toast(message, kind) {
    const k = kind || "info";
    const el = BAM.h("div", { class: `toast toast--${k}`, role: "status" }, message);
    toastHost().appendChild(el);
    const ttl = k === "error" ? 5000 : 3000;
    setTimeout(() => {
      el.style.transition = "opacity 0.2s ease";
      el.style.opacity = "0";
      setTimeout(() => el.remove(), 200);
    }, ttl);
  };

  /* Date formatting ------------------------------------------------------- */

  // Parse "YYYY-MM-DD" as a local date (avoid UTC-midnight off-by-one), or
  // any ISO datetime as-is.
  function toDate(iso) {
    if (!iso) return null;
    if (typeof iso === "string" && /^\d{4}-\d{2}-\d{2}$/.test(iso)) {
      const [y, m, d] = iso.split("-").map(Number);
      return new Date(y, m - 1, d);
    }
    const dt = new Date(iso);
    return isNaN(dt.getTime()) ? null : dt;
  }

  /** Human date, e.g. "Jul 6, 2026". Empty string for null. */
  BAM.fmtDate = function fmtDate(iso) {
    const dt = toDate(iso);
    if (!dt) return "";
    return dt.toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  };

  /** Human date + time, e.g. "Jul 6, 2026, 3:15 PM". Empty string for null. */
  BAM.fmtDateTime = function fmtDateTime(iso) {
    const dt = toDate(iso);
    if (!dt) return "";
    return dt.toLocaleString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  };

  /* Routing --------------------------------------------------------------- */

  const DEFAULT_VIEW = "checkin";

  function mainEl() {
    return document.getElementById("app-main");
  }

  function navEl() {
    return document.getElementById("app-nav");
  }

  // Parse "#checkin?phone=123" -> { name, params }.
  function parseHash() {
    const raw = (location.hash || "").replace(/^#/, "");
    const [name, query] = raw.split("?");
    const params = {};
    if (query) {
      for (const [k, v] of new URLSearchParams(query).entries()) params[k] = v;
    }
    return { name: name || "", params };
  }

  /** Switch to a view by name (updates the hash, triggers a render). */
  BAM.navigate = function navigate(name, params) {
    let hash = `#${name}`;
    if (params && Object.keys(params).length) {
      hash += `?${new URLSearchParams(params).toString()}`;
    }
    if (location.hash === hash) {
      renderCurrent(); // same hash: hashchange won't fire, render manually
    } else {
      location.hash = hash;
    }
  };

  function setActiveNav(name) {
    const nav = navEl();
    if (!nav) return;
    nav.querySelectorAll(".nav__item").forEach((item) => {
      if (item.dataset.view === name) {
        item.setAttribute("aria-current", "page");
      } else {
        item.removeAttribute("aria-current");
      }
    });
  }

  function setBarView(title) {
    const label = document.getElementById("app-bar-view");
    if (label) label.textContent = title || "";
  }

  function renderCurrent() {
    const main = mainEl();
    if (!main) return;
    let { name, params } = parseHash();
    let view = views.get(name);
    if (!view) {
      view = views.get(DEFAULT_VIEW) || BAM.getViews()[0];
      name = view ? view.name : "";
    }
    setActiveNav(name);
    setBarView(view ? view.title : "");

    BAM.clear(main);
    if (!view) {
      main.appendChild(BAM.h("div", { class: "empty-state" }, "No views registered."));
      return;
    }
    main.setAttribute("aria-busy", "false");
    try {
      view.render(main, params || {});
    } catch (err) {
      // A view crash must not blank the whole console.
      console.error(`View "${name}" failed to render:`, err);
      BAM.clear(main);
      main.appendChild(
        BAM.h(
          "div",
          { class: "empty-state" },
          BAM.h("div", { class: "empty-state__icon" }, "⚠️"),
          "Something went wrong rendering this view."
        )
      );
      BAM.toast("This view failed to load.", "error");
    }
  }

  function buildNav() {
    const nav = navEl();
    if (!nav) return;
    BAM.clear(nav);
    BAM.getViews().forEach((view) => {
      const item = BAM.h(
        "a",
        {
          class: "nav__item",
          href: `#${view.name}`,
          dataset: { view: view.name },
          "aria-label": view.title,
        },
        BAM.h("span", { class: "nav__icon", "aria-hidden": "true" }, view.icon || "•"),
        BAM.h("span", { class: "nav__label" }, view.title)
      );
      nav.appendChild(item);
    });
  }

  /** Boot: build the nav, wire hashchange, render the current view. */
  BAM.start = function start() {
    buildNav();
    window.addEventListener("hashchange", renderCurrent);
    renderCurrent();
  };
})();
