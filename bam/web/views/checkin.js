/* Check-in view (spec 6.3) — the primary at-the-distro flow and the
 * reference implementation every other view copies.
 *
 * Flow: enter a phone -> look up the household -> see their open goods and
 * social-service requests -> "Check in" (resets missed count) and/or mark
 * selected requests delivered. Results are re-fetched and re-rendered. */

(function () {
  "use strict";

  const { h, clear, toast, api, fmtDate } = window.BAM;

  // Map a request status to its badge class.
  function statusBadge(status) {
    const cls =
      status === "Delivered"
        ? "badge-delivered"
        : status === "Timeout"
        ? "badge-timeout"
        : "badge-open";
    return h("span", { class: `badge ${cls}` }, status);
  }

  // Appointment status -> badge (booked/checked-in/missed), or nothing.
  function appointmentBadge(status) {
    if (!status) return null;
    const cls =
      status === "Checked-in"
        ? "badge-delivered"
        : status === "Missed"
        ? "badge-timeout"
        : "badge-open";
    return h("span", { class: `badge ${cls}` }, status);
  }

  function render(container, params) {
    // ---- view state ------------------------------------------------------
    const state = {
      view: null, // last CheckinView from the API
      loading: false,
    };
    // Track which request rows are checked, across re-renders keyed by id.
    const selectedRequests = new Set();
    const selectedServices = new Set();

    const heading = h(
      "div",
      { class: "view-heading" },
      h("h1", {}, "Check-in"),
      h("p", { class: "muted" }, "Look up a household by phone to check them in and mark deliveries.")
    );

    // ---- lookup form -----------------------------------------------------
    const phoneInput = h("input", {
      class: "input",
      id: "checkin-phone",
      name: "phone",
      type: "tel",
      inputmode: "tel",
      autocomplete: "off",
      placeholder: "(718) 555-0142",
      "aria-label": "Phone number",
    });

    const lookupBtn = h(
      "button",
      { class: "btn btn-primary", type: "submit" },
      "Look up"
    );

    const form = h(
      "form",
      {
        class: "card stack",
        onsubmit: (e) => {
          e.preventDefault();
          doLookup();
        },
      },
      h(
        "div",
        { class: "field" },
        h("label", { class: "label", for: "checkin-phone" }, "Phone number"),
        phoneInput
      ),
      lookupBtn
    );

    // Result region, replaced on each lookup.
    const result = h("div", { id: "checkin-result" });

    clear(container);
    container.append(heading, form, result);

    // Deep-link support: #checkin?phone=... auto-runs a lookup.
    if (params && params.phone) {
      phoneInput.value = params.phone;
      doLookup();
    } else {
      // Autofocus the phone input on mobile-first arrival.
      setTimeout(() => phoneInput.focus(), 0);
    }

    // ---- actions ---------------------------------------------------------

    async function doLookup() {
      const phone = phoneInput.value.trim();
      if (!phone) {
        toast("Enter a phone number to look up.", "info");
        phoneInput.focus();
        return;
      }
      setBusy(true);
      showLoading("Looking up household…");
      selectedRequests.clear();
      selectedServices.clear();
      try {
        state.view = await api.lookup(phone);
        renderResult();
      } catch (err) {
        state.view = null;
        if (err instanceof api.ApiError && err.status === 404) {
          renderNotFound(phone);
        } else {
          showError(err);
          toast(err.detail || "Lookup failed.", "error");
        }
      } finally {
        setBusy(false);
      }
    }

    async function doCheckIn() {
      if (!state.view) return;
      const id = state.view.household.id;
      setBusy(true);
      try {
        await api.checkIn(id);
        toast("Checked in.", "success");
        await refresh();
      } catch (err) {
        toast(err.detail || "Check-in failed.", "error");
        setBusy(false);
      }
    }

    async function doFulfill() {
      if (!state.view) return;
      const request_ids = [...selectedRequests];
      const social_service_request_ids = [...selectedServices];
      if (!request_ids.length && !social_service_request_ids.length) {
        toast("Select at least one request to mark delivered.", "info");
        return;
      }
      setBusy(true);
      try {
        const out = await api.fulfill({ request_ids, social_service_request_ids });
        const n =
          (out.requests || []).length + (out.social_service_requests || []).length;
        toast(`Marked ${n} delivered.`, "success");
        selectedRequests.clear();
        selectedServices.clear();
        await refresh();
      } catch (err) {
        toast(err.detail || "Could not mark delivered.", "error");
        setBusy(false);
      }
    }

    // Re-fetch the household after a mutation so the UI reflects server truth.
    async function refresh() {
      const phone = state.view.household.phone_number || phoneInput.value.trim();
      try {
        state.view = await api.lookup(phone);
        renderResult();
      } catch (err) {
        // Household may lose its phone (wrong-number outcome) — keep last view.
        if (!(err instanceof api.ApiError && err.status === 404)) {
          toast(err.detail || "Could not refresh.", "error");
        }
      } finally {
        setBusy(false);
      }
    }

    // ---- rendering helpers ----------------------------------------------

    function setBusy(busy) {
      state.loading = busy;
      lookupBtn.disabled = busy;
      lookupBtn.textContent = busy ? "Working…" : "Look up";
    }

    function showLoading(msg) {
      clear(result);
      result.append(
        h("div", { class: "loading" }, h("span", { class: "spinner", role: "status", "aria-label": "Loading" }), msg)
      );
    }

    function showError(err) {
      clear(result);
      result.append(
        h(
          "div",
          { class: "card empty-state" },
          h("div", { class: "empty-state__icon" }, "⚠️"),
          h("div", {}, (err && err.detail) || "Something went wrong."),
          h("button", { class: "btn", onclick: doLookup }, "Try again")
        )
      );
    }

    function renderNotFound(phone) {
      clear(result);
      result.append(
        h(
          "div",
          { class: "card empty-state" },
          h("div", { class: "empty-state__icon" }, "🔍"),
          h("div", {}, `No household found for ${phone}.`),
          h(
            "p",
            { class: "muted" },
            "Double-check the number, or send them to intake to sign up."
          ),
          h(
            "button",
            { class: "btn btn-ghost", onclick: () => window.BAM.navigate("intake") },
            "Go to intake →"
          )
        )
      );
    }

    // Build one selectable request row with a big checkbox.
    function requestRow(req, selectedSet) {
      const checkbox = h("input", {
        type: "checkbox",
        id: `req-${selectedSet === selectedRequests ? "g" : "s"}-${req.id}`,
        checked: selectedSet.has(req.id),
        // Delivered/Timeout rows aren't shown here, but guard anyway.
        disabled: req.status !== "Open",
        onchange: (e) => {
          if (e.target.checked) selectedSet.add(req.id);
          else selectedSet.delete(req.id);
        },
      });
      const label = h(
        "label",
        { class: "list-item__body", for: checkbox.id },
        h("div", { class: "list-item__label" }, req.label || req.type),
        req.request_opened_at
          ? h("div", { class: "list-item__meta" }, `Opened ${fmtDate(req.request_opened_at)}`)
          : null
      );
      return h("li", { class: "list-item list-item--selectable" }, checkbox, label, statusBadge(req.status));
    }

    function requestList(items, selectedSet, emptyMsg) {
      if (!items || !items.length) {
        return h("div", { class: "empty-state" }, h("span", { class: "muted" }, emptyMsg));
      }
      return h(
        "ul",
        { class: "list" },
        items.map((req) => requestRow(req, selectedSet))
      );
    }

    function renderResult() {
      const v = state.view;
      clear(result);
      if (!v) return;

      const hh = v.household;
      const goods = v.open_requests || [];
      const services = v.open_social_service_requests || [];

      // Household summary card.
      const phoneBits = [];
      if (hh.phone_number) phoneBits.push(h("span", { class: "mono" }, hh.phone_number));
      if (hh.invalid_phone_number) phoneBits.push(h("span", { class: "badge badge-timeout" }, "invalid number"));
      if (hh.intl_phone_number) phoneBits.push(h("span", { class: "badge" }, "international"));

      const summary = h(
        "div",
        { class: "card stack" },
        h(
          "div",
          { class: "row row--between" },
          h("div", { class: "grow" }, h("h2", { class: "card__title", style: { margin: "0" } }, hh.name || "Unnamed household")),
          appointmentBadge(hh.appointment_status)
        ),
        phoneBits.length ? h("div", { class: "row" }, phoneBits) : null,
        h(
          "div",
          { class: "row" },
          hh.appointment_date
            ? h(
                "span",
                { class: "pill" },
                `Appt ${fmtDate(hh.appointment_date)}${hh.appointment_time ? " · " + hh.appointment_time : ""}`
              )
            : null,
          hh.missed_appointment_count
            ? h("span", { class: "pill" }, `${hh.missed_appointment_count} missed`)
            : null,
          hh.languages && hh.languages.length
            ? h("span", { class: "pill" }, hh.languages.join(", "))
            : null
        ),
        // Primary check-in action.
        h(
          "button",
          {
            class: "btn btn-primary btn-block",
            onclick: doCheckIn,
            disabled: state.loading,
          },
          hh.appointment_status === "Checked-in" ? "Check in again" : "Check in"
        )
      );

      // Requests card with the two grouped lists + fulfill action.
      const totalOpen = goods.length + services.length;
      const requestsCard = h(
        "div",
        { class: "card stack" },
        h("h2", { class: "card__title" }, "Open requests"),
        totalOpen === 0
          ? h(
              "div",
              { class: "empty-state" },
              h("div", { class: "empty-state__icon" }, "✅"),
              h("div", {}, "No open requests — everything delivered.")
            )
          : [
              h("div", { class: "section-title" }, `Goods (${goods.length})`),
              requestList(goods, selectedRequests, "No open goods requests."),
              h("div", { class: "section-title" }, `Social services (${services.length})`),
              requestList(services, selectedServices, "No open social service requests."),
              h(
                "button",
                {
                  class: "btn btn-primary btn-block",
                  onclick: doFulfill,
                  disabled: state.loading,
                },
                "Mark selected delivered"
              ),
            ]
      );

      result.append(summary, requestsCard);
    }
  }

  window.BAM.registerView("checkin", {
    title: "Check-in",
    icon: "✅",
    render,
  });
})();
