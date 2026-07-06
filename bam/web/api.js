/* BAM API client — thin same-origin fetch wrapper.
 * All methods are async and throw ApiError on any non-2xx response.
 * Attaches itself to window.BAM.api (creating the namespace if needed so
 * load order relative to app.js is irrelevant). */

(function () {
  "use strict";

  /** Error thrown for any non-2xx response. `detail` is the human message. */
  class ApiError extends Error {
    constructor(status, detail, body) {
      super(detail || `Request failed (${status})`);
      this.name = "ApiError";
      this.status = status;
      this.detail = detail;
      this.body = body;
    }
  }

  // Extract a human-friendly message from a FastAPI error body.
  function extractDetail(status, body) {
    if (body && typeof body === "object") {
      const d = body.detail;
      if (typeof d === "string") return d;
      // 422 validation errors arrive as a list of {loc, msg, ...}.
      if (Array.isArray(d) && d.length) {
        return d
          .map((e) => {
            const loc = Array.isArray(e.loc) ? e.loc.slice(1).join(".") : "";
            return loc ? `${loc}: ${e.msg}` : e.msg;
          })
          .join("; ");
      }
    }
    if (typeof body === "string" && body) return body;
    return `Request failed (${status})`;
  }

  async function request(method, path, body) {
    const opts = { method, headers: { Accept: "application/json" } };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }

    let res;
    try {
      res = await fetch(path, opts);
    } catch (networkErr) {
      // fetch only rejects on network failure, not HTTP status.
      throw new ApiError(0, "Network error — is the server reachable?", networkErr);
    }

    // Parse body once, tolerating empty/non-JSON responses.
    const text = await res.text();
    let parsed = null;
    if (text) {
      try {
        parsed = JSON.parse(text);
      } catch (_e) {
        parsed = text;
      }
    }

    if (!res.ok) {
      throw new ApiError(res.status, extractDetail(res.status, parsed), parsed);
    }
    return parsed;
  }

  // Build a query string, skipping null/undefined values.
  function qs(params) {
    const usp = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null) usp.set(k, v);
    }
    const s = usp.toString();
    return s ? `?${s}` : "";
  }

  const api = {
    ApiError,

    // Primitives -----------------------------------------------------------
    get(path) {
      return request("GET", path);
    },
    post(path, body) {
      return request("POST", path, body === undefined ? {} : body);
    },

    // Check-in (spec 6.3) --------------------------------------------------
    lookup(phone) {
      return request("GET", `/households/lookup${qs({ phone })}`);
    },
    searchByName(name) {
      return request("GET", `/households/search${qs({ name })}`);
    },
    householdView(id) {
      return request("GET", `/households/${id}`);
    },
    checkIn(id) {
      return request("POST", `/households/${id}/checkin`, {});
    },
    fulfill({ request_ids = [], social_service_request_ids = [] } = {}) {
      return request("POST", "/requests/fulfill", {
        request_ids,
        social_service_request_ids,
      });
    },

    // Intake (spec 6.1) ----------------------------------------------------
    intake(payload) {
      return request("POST", "/intake/submissions", payload);
    },

    // Outreach (spec 6.2 + A4–A6) -----------------------------------------
    outreachList(filters) {
      return request("POST", "/outreach/list", filters || {});
    },
    blast({ household_ids = [], template, max_messages } = {}) {
      const body = { household_ids, template };
      if (max_messages !== undefined && max_messages !== null) {
        body.max_messages = max_messages;
      }
      return request("POST", "/outreach/blast", body);
    },
    bookAppointment(id, { appointment_date, appointment_time }) {
      return request("POST", `/households/${id}/appointment`, {
        appointment_date,
        appointment_time,
      });
    },
    recordOutcome(id, { outcome, note }) {
      const body = { outcome };
      if (note !== undefined && note !== null) body.note = note;
      return request("POST", `/households/${id}/outreach-outcome`, body);
    },

    // Distros (spec 6.3 / no-shows) ---------------------------------------
    createDistro(body) {
      return request("POST", "/distros", body);
    },
    listDistros() {
      return request("GET", "/distros");
    },
    noShows({ distro_date }) {
      return request("POST", "/distros/no-shows", { distro_date });
    },

    // Jobs (cron endpoints) ------------------------------------------------
    expire() {
      return request("POST", "/jobs/expire", {});
    },
    websiteData() {
      return request("POST", "/jobs/website-data", {});
    },
    scrubPii() {
      return request("POST", "/jobs/scrub-pii", {});
    },

    // Metrics (spec 5) -----------------------------------------------------
    openRequests() {
      return request("GET", "/metrics/open-requests");
    },
    fulfilled({ start, end } = {}) {
      return request("GET", `/metrics/fulfilled${qs({ start, end })}`);
    },

    // Catalog (request types + languages; single source of truth) ----------
    catalog() {
      if (!this._catalogPromise) {
        this._catalogPromise = request("GET", "/catalog");
      }
      return this._catalogPromise;
    },
  };

  window.BAM = window.BAM || {};
  window.BAM.api = api;
})();
