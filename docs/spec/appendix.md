# BAM Mutual Aid System V2 - Appendix

## Post-MVP Flows

### A.1 Delivery / Transport Flow

This flow is primarily text-based coordination through group messaging and does not require system integration in the MVP.

1. **Coordinator** sends message to BAM group requesting transport help
2. **Volunteer** shows up at pickup location with vehicle
3. **Team** loads items into vehicle
4. **Volunteer** drives to destination
5. **Team** unloads items at destination

**Post-condition:** Items transported to destination

---

### A.2 Donate / Volunteer Flow

Form submissions and follow-up are handled manually outside the MVP system. Furniture donations have a separate flow handled by the furniture team.

1. **User** submits donate/volunteer form
2. **System** records submission
3. **Admin** reviews and follows up as needed

**Post-condition:** Donation/volunteer interest recorded

---

## Feature Suggestions

The following features could improve system efficiency, user experience, and operational scalability. These are suggestions for future consideration, not requirements for the MVP.

### A.3 Automation & Efficiency

#### Automated Outreach Targeting
**Problem:** Admins manually curate the outreach list for each text blast.
**Suggestion:** Auto-generate target lists based on:
- Current inventory levels
- Volunteer language availability for upcoming distro
- Recipients who haven't attended in X days
- Request age prioritization

With mandatory admin approval before sending.

**Value:** Reduces admin prep time, ensures consistent targeting criteria, minimizes human error.

#### Automated Text Blast Retries
**Problem:** No follow-up for recipients who don't respond to the initial text blast.
**Suggestion:** Automated escalation:
- Auto-send follow-up texts on a schedule (up to 3x)
- Flag for phone call after text failures
- Track attempts per household

**Value:** Consistent follow-up without volunteer tracking burden.

#### Automated Appointment Booking
**Problem:** Volunteers manually respond to each confirmation and update the system.
**Suggestion:** Self-service booking where recipients:
- Receive text with available time slots
- Reply with slot number to auto-book
- Get confirmation with appointment details

**Value:** Reduces volunteer workload during outreach shifts, faster booking turnaround.

#### Language-Aware Routing
**Problem:** Manual matching of volunteer languages to recipient needs.
**Suggestion:** System matches:
- Volunteer language skills to recipient preferences
- Auto-assign outreach based on language match
- Alert when no language match available

**Value:** Better recipient experience, more efficient volunteer utilization.

---

### A.4 Inventory Management

#### Post-Distro Inventory Reporting
**Problem:** Post-distro inventory is informal text-based reporting with no system record.
**Suggestion:** Digital inventory log replacing the current text format:
```
POST DISTRO INVENTORY [DATE]
Basement inventory:
Buyer: [Name]  |  Inventory: [Name]
Diapers: 1: X boxes / 2: X boxes / ...
Pads: X packs | Soap: X boxes | School Supplies: X boxes | Kitchen: description
```
- Structured form for volunteers to submit post-distro counts
- Historical inventory records per distro date
- Feeds into pre-distro planning

**Value:** Replaces informal text coordination, enables trend analysis across distros.

#### Real-Time Inventory Tracking
**Problem:** No visibility into current stock levels during distro planning.
**Suggestion:** Digital inventory system with:
- Pre-distro stock counts
- Real-time deduction during check-in
- Low-stock alerts
- Reorder suggestions

**Value:** Better distro planning, prevents over-promising items not in stock.

#### Inventory-Aware Request Matching
**Problem:** Admins manually match available supplies to request types.
**Suggestion:** System auto-filters outreach to:
- Only contact households requesting available items
- Prioritize items with excess inventory
- Defer low-stock item requests

**Value:** Higher fulfillment rate per distro, reduces partial fulfillments.

---

### A.5 Recipient Experience

#### Request Status Portal
**Problem:** Recipients have no visibility into request status.
**Suggestion:** Simple web/SMS interface showing:
- Current request status (Open/Scheduled/Fulfilled)
- Position in queue
- Estimated wait time
- Next distro dates

**Value:** Reduces inquiry volume, builds trust through transparency.

#### Appointment Reminders
**Problem:** No automated reminders before appointments.
**Suggestion:** Send reminders:
- 24 hours before appointment
- 2 hours before appointment
- Include location, time, what to bring

**Value:** Reduces no-show rate, improves distro efficiency.

#### Multi-Channel Notifications
**Problem:** SMS-only communication limits reach.
**Suggestion:** Support multiple channels:
- SMS (primary)
- Email (backup)
- WhatsApp (for international numbers)

**Value:** Better reach, accommodates communication preferences.

#### Configurable Expiration Windows
**Problem:** Fixed 14-day expiration may not suit all request types.
**Suggestion:** Per-request-type expiration:
- Urgent items (diapers, pads): 7 days
- Standard goods: 14 days
- Furniture/large items: 30-60 days
- Social services: 30 days

**Value:** Better matches urgency to item availability patterns.

---

### A.6 Volunteer Management

#### Shift Scheduling System
**Problem:** Manual coordination for distro staffing.
**Suggestion:** Volunteer scheduling with:
- Available shift slots per distro
- Self-service sign-up
- Language skill matching
- Automated reminders
- No-show tracking

**Value:** Easier coordination, better language coverage.

#### Volunteer Onboarding Workflow
**Problem:** Onboarding process unclear.
**Suggestion:** Structured onboarding:
- Automated welcome sequence
- Training module completion tracking
- Shadowing assignment
- Probation period management

**Value:** Consistent onboarding, faster time-to-productivity.

#### Access Management
**Problem:** Volunteer access revocation timeline unclear.
**Suggestion:** Automated access lifecycle:
- Inactivity alerts (30/60/90 days)
- Auto-revoke after X days inactive
- Re-onboarding for returning volunteers
- Audit trail for access changes

**Value:** Security, compliance, clean volunteer roster.

---

### A.7 Data Quality

#### Phone Number Validation
**Problem:** Invalid/international numbers cause outreach failures.
**Suggestion:** At intake:
- Format validation
- Carrier lookup
- International number flagging
- Duplicate detection

**Value:** Cleaner data, fewer failed outreach attempts.

#### Household Deduplication Tools
**Problem:** Multiple phone numbers create duplicate households.
**Suggestion:** Admin tools for:
- Duplicate detection reports
- Merge household records
- Link multiple phones to one household
- Audit trail for merges

**Value:** Accurate household counts, prevents double-fulfillment.

#### Configurable Data Retention
**Problem:** No clear policy for old data.
**Suggestion:** Automated data lifecycle:
- Archive fulfilled requests after X days
- Configurable per data type

**Value:** Privacy compliance, database performance.

---

### A.8 Reporting & Analytics

#### Operations Dashboard
**Problem:** Metrics require manual aggregation.
**Suggestion:** Real-time dashboard showing:
- Open requests by type
- Fulfillment rate trends
- No-show rates
- Inventory levels
- Volunteer activity

**Value:** Data-driven decisions, early problem detection.

#### Distribution Planning Reports
**Problem:** Manual analysis for distro planning.
**Suggestion:** Auto-generated reports:
- Optimal target list size for capacity
- Language coverage gaps
- Geographic distribution
- Historical attendance patterns

**Value:** Better planning, improved efficiency.

#### Impact Reporting
**Problem:** Limited visibility into program impact.
**Suggestion:** Generate reports for:
- Households served over time
- Requests fulfilled by type
- Average time-to-fulfillment
- Community reach by neighborhood

**Value:** Fundraising support, stakeholder communication.

---

### A.9 Integration & Infrastructure

#### API for External Systems
**Problem:** Limited integration capabilities.
**Suggestion:** REST API supporting:
- Read/write for all tables
- Webhook subscriptions
- Rate limiting
- Authentication/authorization

**Value:** Enables partner integrations, custom tooling.

#### Mobile Check-In App
**Problem:** Current interface not optimized for mobile.
**Suggestion:** Dedicated mobile app for:
- Phone number lookup
- Request display
- Quick fulfillment marking
- Offline support

**Value:** Faster check-ins, works in low-connectivity venues.

#### Backup & Disaster Recovery
**Problem:** Single point of failure in current system.
**Suggestion:** Implement:
- Daily automated backups
- Point-in-time recovery
- Failover procedures
- Recovery testing schedule

**Value:** Data protection, operational continuity.

---

### A.10 Priority Recommendations

| Priority | Feature | Impact | Effort |
|----------|---------|--------|--------|
| **P0** | Appointment Reminders | High - reduces no-shows | Low |
| **P0** | Phone Number Validation | High - improves data quality | Low |
| **P1** | Automated Outreach Targeting | High - saves admin time | Medium |
| **P1** | Post-Distro Inventory Reporting | High - better planning | Medium |
| **P1** | Operations Dashboard | High - visibility | Medium |
| **P2** | Automated Appointment Booking | Medium - reduces volunteer load | Medium |
| **P2** | Volunteer Shift Scheduling | Medium - easier coordination | Medium |
| **P2** | Household Deduplication Tools | Medium - data quality | Medium |
| **P3** | Request Status Portal | Medium - recipient experience | High |
| **P3** | Mobile Check-In App | Medium - faster check-ins | High |
| **P3** | Language-Aware Routing | Medium - better matching | High |
