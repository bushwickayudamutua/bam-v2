/**
 * CRDT-backed drop-in replacement for the console's `BAM.api`.
 *
 * The operator console (bam/web/views/*.js, copied verbatim into
 * web/public/console/) was written against the FastAPI JSON surface. This
 * adapter implements the same method names and snake_case response shapes
 * over the local Automerge store, so the views run unchanged with no
 * server at all. Errors are thrown as `ApiError` with `status`/`detail`,
 * matching what the views expect from the fetch wrapper.
 */

import type { BamStore } from "./store.ts";
import type {
  BamDoc,
  Distro,
  Household,
  RequestRow,
  SocialServiceRequestRow,
} from "./schema.ts";
import { newId, nowIso } from "./schema.ts";
import {
  GOODS,
  LANGUAGES,
  SOCIAL_SERVICES,
  labelFor,
} from "./domain/catalog.ts";
import { submitIntake } from "./domain/intake.ts";
import type { CheckinView } from "./domain/checkin.ts";
import {
  buildCheckinView,
  checkIn,
  fulfill,
  lookupByPhone,
  processNoShows,
  searchByName,
} from "./domain/checkin.ts";
import {
  buildOutreachList,
  confirmAppointment,
  queueBlast,
  recordOutcome,
  type OutreachOutcome,
} from "./domain/outreach.ts";
import { expireStale, scrubExpiredPii } from "./domain/lifecycle.ts";
import { fulfilledCountsRange, openRequestCounts } from "./domain/metrics.ts";

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

function householdOut(h: Household): Record<string, unknown> {
  return {
    id: h.id,
    name: h.name ?? null,
    phone_number: h.phoneNumber ?? null,
    invalid_phone_number: h.invalidPhoneNumber,
    intl_phone_number: h.intlPhoneNumber,
    email: h.email ?? null,
    email_error: h.emailError ?? null,
    languages: h.languages,
    notes: h.notes ?? null,
    appointment_date: h.appointmentDate ?? null,
    appointment_time: h.appointmentTime ?? null,
    appointment_status: h.appointmentStatus ?? null,
    missed_appointment_count: h.missedAppointmentCount,
    last_texted: h.lastTexted ?? null,
    last_attended: h.lastAttended ?? null,
  };
}

function requestOut(r: RequestRow | SocialServiceRequestRow): Record<string, unknown> {
  return {
    id: r.id,
    type: r.type,
    label: labelFor(r.type),
    status: r.status,
    request_opened_at: r.requestOpenedAt,
    processing_date: r.processingDate ?? null,
    notes: r.notes ?? null,
  };
}

function checkinViewOut(view: CheckinView): Record<string, unknown> {
  return {
    household: householdOut(view.household),
    open_requests: view.openRequests.map(requestOut),
    open_social_service_requests: view.openSocialServiceRequests.map(requestOut),
    delivered_request_types: view.deliveredRequestTypes,
  };
}

function distroOut(d: Distro): Record<string, unknown> {
  return {
    id: d.id,
    date_time: d.dateTime,
    location: d.location ?? null,
    duration_minutes: d.durationMinutes ?? null,
    appointments: d.appointments ?? null,
    notes: d.notes ?? null,
  };
}

function wrap<T>(fn: () => T): T {
  try {
    return fn();
  } catch (err) {
    if (err instanceof ApiError) throw err;
    const message = err instanceof Error ? err.message : String(err);
    throw new ApiError(/unknown/i.test(message) ? 404 : 400, message);
  }
}

/** Build the `BAM.api`-compatible adapter over an open store. */
export function makeWebApi(store: BamStore) {
  const doc = (): BamDoc => store.base.doc();

  return {
    ApiError,

    // Check-in (spec 6.3) --------------------------------------------------
    async lookup(phone: string) {
      const view = lookupByPhone(doc(), phone);
      if (!view) throw new ApiError(404, `No household with phone '${phone}'`);
      return checkinViewOut(view);
    },
    async searchByName(name: string) {
      return searchByName(doc(), name).map((h) => ({
        id: h.id,
        name: h.name ?? null,
        phone_number: h.phoneNumber ?? null,
        languages: h.languages,
      }));
    },
    async householdView(id: string) {
      const h = doc().households[id];
      if (!h) throw new ApiError(404, `Unknown household id ${id}`);
      return checkinViewOut(buildCheckinView(doc(), h));
    },
    async checkIn(id: string) {
      return householdOut(wrap(() => checkIn(store.base, id)));
    },
    async fulfill(body: { request_ids?: string[]; social_service_request_ids?: string[] } = {}) {
      const requestIds = body.request_ids ?? [];
      const socialIds = body.social_service_request_ids ?? [];
      wrap(() =>
        fulfill(store.base, { requestIds, socialServiceRequestIds: socialIds })
      );
      const after = doc();
      return {
        requests: requestIds.map((id) => requestOut(after.requests[id]!)),
        social_service_requests: socialIds.map((id) =>
          requestOut(after.socialServiceRequests[id]!)
        ),
      };
    },

    // Intake (spec 6.1) ----------------------------------------------------
    async intake(payload: Record<string, unknown>) {
      const result = await submitIntake(store.base, {
        phoneNumber: String(payload.phone_number ?? ""),
        name: (payload.name as string) ?? undefined,
        email: (payload.email as string) ?? undefined,
        languages: (payload.languages as string[]) ?? [],
        requestTypes: (payload.request_types as string[]) ?? [],
        furnitureItems: (payload.furniture_items as string[]) ?? [],
        bedDetails: (payload.bed_details as string[]) ?? [],
        kitchenItems: (payload.kitchen_items as string[]) ?? [],
        socialServiceRequests: (payload.social_service_requests as string[]) ?? [],
        internetAccess: (payload.internet_access as string[]) ?? [],
        roofAccessible: !!payload.roof_accessible,
        notes: (payload.notes as string) ?? undefined,
        streetAddress: (payload.street_address as string) ?? undefined,
        cityState: (payload.city_state as string) ?? undefined,
        zipCode: payload.zip_code != null ? String(payload.zip_code) : undefined,
      });
      return {
        submission_id: 0, // no separate submissions table in the CRDT model
        household_id: result.householdId,
        created_household: result.createdHousehold,
        created_request_ids: result.createdRequestIds,
        created_social_service_request_ids: result.createdSocialServiceRequestIds,
        skipped_duplicate_types: result.skippedDuplicateTypes,
        unknown_types: result.unknownTypes,
        phone_valid: result.phoneValid,
        already_processed: false,
      };
    },

    // Outreach (spec 6.2 + A4-A6) -------------------------------------------
    async outreachList(filters: Record<string, unknown> = {}) {
      return buildOutreachList(doc(), {
        requestTypes: (filters.request_types as string[]) ?? undefined,
        languages: (filters.languages as string[]) ?? undefined,
        excludeTextedWithinDays: (filters.exclude_texted_within_days as number) ?? 0,
        excludeAttendedWithinDays: (filters.exclude_attended_within_days as number) ?? 0,
        limit: (filters.limit as number) ?? undefined,
      }).map((c) => ({
        household_id: c.householdId,
        name: c.name ?? null,
        phone_number: c.phoneNumber ?? null,
        languages: c.languages,
        open_request_types: c.openRequestTypes,
        oldest_open_request_at: c.oldestOpenRequestAt ?? null,
        last_texted: c.lastTexted ?? null,
      }));
    },
    async blast(body: { household_ids?: string[]; template?: string; max_messages?: number } = {}) {
      const report = queueBlast(
        store.base,
        {
          householdIds: body.household_ids ?? [],
          template: body.template ?? "",
          maxMessages: body.max_messages ?? undefined,
        },
        nowIso(),
        store.peerId
      );
      return {
        sent: report.sent,
        failed: 0, // messages are queued to the shared outbox, not sent inline
        skipped_invalid: report.skippedInvalid,
        skipped_no_phone: report.skippedNoPhone,
        not_sent_over_limit: report.notSentOverLimit,
        unknown_household_ids: report.unknownHouseholdIds,
        messages: report.messages.map((m) => ({
          household_id: m.householdId,
          to: m.to,
          body: m.body,
          ok: true,
          error: null,
        })),
      };
    },
    async bookAppointment(
      id: string,
      body: { appointment_date: string; appointment_time: string }
    ) {
      return householdOut(
        wrap(() =>
          confirmAppointment(store.base, id, {
            date: body.appointment_date,
            time: body.appointment_time,
          })
        )
      );
    },
    async recordOutcome(id: string, body: { outcome: string; note?: string | null }) {
      return householdOut(
        wrap(() =>
          recordOutcome(
            store.base,
            id,
            body.outcome as OutreachOutcome,
            body.note ?? undefined
          )
        )
      );
    },

    // Distros ----------------------------------------------------------------
    async createDistro(body: Record<string, unknown>) {
      const id = newId();
      const now = nowIso();
      store.base.change((d) => {
        const row: Distro = {
          id,
          dateTime: String(body.date_time),
          createdAt: now,
        };
        if (body.location) row.location = String(body.location);
        if (body.duration_minutes != null) row.durationMinutes = Number(body.duration_minutes);
        if (body.appointments != null) row.appointments = String(body.appointments);
        if (body.notes) row.notes = String(body.notes);
        d.distros[id] = row;
      });
      return distroOut(doc().distros[id]!);
    },
    async listDistros() {
      return Object.values(doc().distros)
        .sort((a, b) => (a.dateTime < b.dateTime ? -1 : 1))
        .map(distroOut);
    },
    async noShows(body: { distro_date: string }) {
      const report = wrap(() => processNoShows(store.base, body.distro_date));
      return {
        missed_household_ids: report.missedHouseholdIds,
        timed_out_household_ids: report.timedOutHouseholdIds,
      };
    },

    // Jobs --------------------------------------------------------------------
    async expire() {
      const report = expireStale(store.base);
      return {
        timed_out_request_ids: report.timedOutRequestIds,
        timed_out_social_service_request_ids: report.timedOutSocialServiceRequestIds,
      };
    },
    async websiteData() {
      const counts = openRequestCounts(doc());
      return { generated_at: counts.generatedAt, counts: counts.counts };
    },
    async scrubPii() {
      const report = await scrubExpiredPii(store.base);
      return {
        households_anonymized: report.householdsAnonymized,
        requests_scrubbed: report.requestsScrubbed,
        social_service_requests_scrubbed: report.socialServiceRequestsScrubbed,
        submissions_scrubbed: 0,
      };
    },

    // Metrics -------------------------------------------------------------------
    async openRequests() {
      const counts = openRequestCounts(doc());
      return { generated_at: counts.generatedAt, counts: counts.counts };
    },
    async fulfilled(range: { start?: string; end?: string } = {}) {
      return fulfilledCountsRange(doc(), range);
    },

    // Catalog ---------------------------------------------------------------------
    async catalog() {
      const entry = (t: { key: string; label: string; category: string }) => ({
        key: t.key,
        label: t.label,
        category: t.category,
      });
      return {
        goods: GOODS.map(entry),
        social_services: SOCIAL_SERVICES.map(entry),
        languages: [...LANGUAGES],
      };
    },
  };
}
