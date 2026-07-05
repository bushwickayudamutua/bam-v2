# BAM Mutual Aid System - Current System Background

This document describes the existing system architecture and workflows. It serves as reference for understanding the current state before improvements.

---

## 1. System Overview

Bushwick Ayuda Mutua (BAM) operates a mutual aid system that manages intake requests, distribution events, and volunteer coordination through a combination of Airtable, Digital Ocean functions, and manual processes.

### Technology Stack
- **Database:** Airtable
- **Automation:** Digital Ocean Functions
- **SMS:** Dialpad API
- **Email:** Mailjet
- **Address Validation:** Google Maps API + NYC Planning Labs
- **File Storage:** Digital Ocean Spaces (S3-compatible)
- **Forms:** Fillout (multi-language)

### GitHub Repository
- **Repo:** [github.com/bushwickayudamutua/bam-automation](https://github.com/bushwickayudamutua/bam-automation)
- **Core Library:** [`/core`](https://github.com/bushwickayudamutua/bam-automation/tree/main/core) - Reusable Python utilities (Airtable, SMS, email clients)
- **Functions:** [`/functions`](https://github.com/bushwickayudamutua/bam-automation/tree/main/functions) - Digital Ocean serverless functions
- **API App:** [`/app`](https://github.com/bushwickayudamutua/bam-automation/tree/main/app) - FastAPI application for extended functionality
- **Entry Point:** [`functions/project.yml`](https://github.com/bushwickayudamutua/bam-automation/blob/main/functions/project.yml)

---

## 2. Current Outreach Flowchart

![BAM Outreach Flowchart](./bam-outreach-flowchart.png)

*Current outreach process: automated text blasts, retry logic (3x text, then call, then email), timeout handling*

---

## 3. Existing Automation Functions

### Scheduled Jobs (Cron)

| Function | Schedule | Purpose | Source Code |
|----------|----------|---------|-------------|
| `UpdateWebsiteRequestData` | Hourly | Publishes open request counts to website JSON | [`website/update_request_data`](https://github.com/bushwickayudamutua/bam-automation/tree/main/functions/packages/website/update_request_data) |
| `DedupeAirtableViews` | Daily (10:33 PM ET) | Deduplicates records by phone across 23 views | [`airtable/dedupe_views`](https://github.com/bushwickayudamutua/bam-automation/tree/main/functions/packages/airtable/dedupe_views) |
| `UpdateMailjetLists` | Daily | Syncs contacts to Mailjet email lists | [`mailjet/update_lists`](https://github.com/bushwickayudamutua/bam-automation/tree/main/functions/packages/mailjet/update_lists) |
| `SnapshotAirtableViews` | Daily | Backs up modified records to S3 | *(not found in current repo)* |

**Cron Job Runners:**
- [Hourly cron](https://github.com/bushwickayudamutua/bam-automation/tree/main/functions/packages/cron/hourly) - Executes hourly scheduled functions
- [Daily cron](https://github.com/bushwickayudamutua/bam-automation/tree/main/functions/packages/cron/daily) - Executes daily scheduled functions

### Web-Triggered Functions

| Function | Purpose | Source Code |
|----------|---------|-------------|
| `send_dialpad_sms` | Sends SMS text blasts via Dialpad API | [`airtable/send_dialpad_sms`](https://github.com/bushwickayudamutua/bam-automation/tree/main/functions/packages/airtable/send_dialpad_sms) |
| `consolidate_eg_requests` | Consolidates requests when household needs multiple items | [`airtable/consolidate_eg_requests`](https://github.com/bushwickayudamutua/bam-automation/tree/main/functions/packages/airtable/consolidate_eg_requests) |
| `timeout_eg_requests` | Times out old unfulfilled requests when newer ones fulfilled | [`airtable/timeout_eg_requests`](https://github.com/bushwickayudamutua/bam-automation/tree/main/functions/packages/airtable/timeout_eg_requests) |
| `update_field_value` | Bulk updates field for multiple phone numbers | [`airtable/update_field_value`](https://github.com/bushwickayudamutua/bam-automation/tree/main/functions/packages/airtable/update_field_value) |
| `/clean-record` API | Validates/normalizes phone, email, address | [FastAPI app](https://github.com/bushwickayudamutua/bam-automation/tree/main/app) |

### External Service Integrations

| Service | Purpose |
|---------|---------|
| Airtable | Primary database |
| Dialpad | SMS messaging |
| Mailjet | Email list management |
| Google Maps | Address normalization |
| NYC Planning Labs | Geospatial address lookup |
| Digital Ocean Spaces | File storage/CDN for snapshots |

---

## 4. Current Airtable Schema

**Note:** The current production Airtable schema is not documented here, as access to the production base is restricted. The V2 specification defines the new schema at [bam-mutual-aid-spec.md](./bam-mutual-aid-spec.md#4-data-schema).

**Known Tables (from automation code):**
- Households (recipient households)
- Requests (goods/services requests)
- Social Service Requests
- Distros (distribution events)
- Fulfilled Request Count (metrics)
- Assistance Request Form Submissions (intake data)

---

## 5. Request Type Categories

### Essential Goods
- **Toiletries:** Soap, Pads, Baby Diapers, Adult Diapers
- **Household:** Clothing, School Supplies, Stroller, Pet Food

### Kitchen Items
- Pots & Pans, Plates, Cups, Utensils, Microwave, Coffee Maker, Blender

### Furniture
- **Beds:** Crib through King (mattress/frame options)
- Sofa, Dresser, Desk, Coffee Table, Chairs, Storage, Dining Table, Fridge, AC

### Food Requests
- Groceries, Hot meals

### Social Services
- Housing, Health Insurance, English Classes, Transportation
- Tenant legal, In-school services, Tutoring, Business support
- Internet, Food benefits, Child disability, Pet assistance

---

## 6. Multi-Language Support

### Supported Languages
- English, Spanish, Mandarin, Cantonese, Toishanese
- Quechua, Portuguese, Haitian Creole, Tagalog, Arabic, French

### Trilingual Format
All request names stored in format:
```
"Jabón & Productos de baño / Soap & Shower Products / 肥皂和淋浴用品"
```

---

## 7. Current Operational Metrics

- **Distribution frequency:** 3 appointment-based distributions per week
- **Appointments per distribution:** ~60
- **Outreach target:** 240 people (25% hit rate)
- **Request expiration:** 14 days (30 days for Pots & Pans)

---

## 8. Known Issues & Technical Debt

### Data Privacy
- Addresses stored in plain text for furniture/delivery requests (hashing would break logistics)
- PII not properly anonymized after fulfillment

### Edge Cases
- Multiple households sharing same phone number causes deduplication issues
- Multiple phone numbers per household creates duplicate households
- Language matching between volunteers and recipients is manual

### Concessions
- 14-day expiration window may be too short for some needs
- Text blast targeting requires manual view creation
- Post-distro inventory is informal text-based reporting

---

## 9. Form URLs by Language

| Language | Form URL |
|----------|----------|
| English | https://forms.fillout.com/t/ivajQbwoWxus |
| Spanish | https://forms.fillout.com/t/sevuKn32WBus |
| Chinese (Traditional) | https://forms.fillout.com/t/docSKMdPyBus |
| French | https://forms.fillout.com/t/dDXKMJ1Fjqus |
| Arabic | https://forms.fillout.com/t/fAM7NKL8LPus |

---

## 10. Links & References

### Documentation
- **V2 Specification:** [bam-mutual-aid-spec.md](./bam-mutual-aid-spec.md)
- **Platform Research:** [platform-research-summary.md](./platform-research-summary.md)

### Code Repositories
- **Automation repo:** [github.com/bushwickayudamutua/bam-automation](https://github.com/bushwickayudamutua/bam-automation)
- **Core utilities:** [`/core`](https://github.com/bushwickayudamutua/bam-automation/tree/main/core)
- **Serverless functions:** [`/functions`](https://github.com/bushwickayudamutua/bam-automation/tree/main/functions)
- **FastAPI app:** [`/app`](https://github.com/bushwickayudamutua/bam-automation/tree/main/app)
- **Analysis notebooks:** [`/notebooks`](https://github.com/bushwickayudamutua/bam-automation/tree/main/notebooks)

### Key Files
- **Function configuration:** [`functions/project.yml`](https://github.com/bushwickayudamutua/bam-automation/blob/main/functions/project.yml)
- **README:** [Setup & development docs](https://github.com/bushwickayudamutua/bam-automation/blob/main/README.md)

### Function Endpoints
- **SMS text blast:** [`send_dialpad_sms`](https://github.com/bushwickayudamutua/bam-automation/tree/main/functions/packages/airtable/send_dialpad_sms)
- **Website data:** [`update_request_data`](https://github.com/bushwickayudamutua/bam-automation/tree/main/functions/packages/website/update_request_data)
- **Record validation:** `/clean-record` API endpoint
