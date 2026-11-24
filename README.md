# AgriNex

AgriNex is a farmer-first farm-to-family marketplace. The current feature branch contains a FastAPI API, a dedicated Supabase database, the customer/farmer marketplace, bulk buyer quotations, and provider adapters.

> **Preview / pilot scope:** All development remains on `feature/phone-sms-auth`. The branch is not merged into `main`. Do not use it for live commerce until a responsible adult-operated legal entity, a reviewed operating process, provider onboarding, and end-to-end acceptance tests are in place.

## What is implemented

### Customers and business buyers
- Separate customer and farmer phone/password registration screens.
- Six-digit SMS confirmation is part of registration; the live SMS provider still needs configuration in Supabase Auth.
- Active listings from approved farmers, saved delivery addresses, and a server-backed cart.
- COD checkout with order and stock validation inside a database transaction.
- Razorpay **test-mode adapter**: creates a gateway test order, verifies the checkout signature server-side, checks the captured payment and amount with the provider, and reconciles signed webhook events idempotently.
- Bulk procurement requests and farmer quotations. Accepting a quotation does not itself create a confirmed order.

### Farmers
- Pending, approved and rejected verification states with a private admin approval queue.
- Server-backed product listings, inventory, images, order processing, notifications and delivered-sales summaries.
- Draft listings while pending; publishing, bulk offers and order processing require approval.
- Browser voice input (where supported) and photo-assisted product creation.
- Text-only offline draft sync. Images are not cached offline and must be selected again after reconnecting.
- Tamil/English shared UI translation and saved language preference. The translation dictionary is broad for core flows but still requires a human review; it is not yet a professionally reviewed complete localization.

### Market and fulfilment data
- Daily AGMARKNET/data.gov.in price-feed adapter. It imports dated wholesale observations, not real-time prices. The server needs a data.gov.in API key.
- Farmer demand summaries use AgriNex's own order records only. They are not a measure of total local market demand.
- Shiprocket adapter for serviceability, booking, AWB and pickup requests. Live booking is deliberately blocked in this student pilot.
- WhatsApp Cloud API template adapter with explicit per-user opt-in. Provider acceptance is not proof of message delivery; delivery webhooks are not yet integrated.
- Settlement-readiness calculations distinguish delivered sales from actually collected COD or captured prepaid payments. Automatic transfers are disabled.

## Integration status

| Integration | Code status | External activation |
| --- | --- | --- |
| SMS OTP | Supabase phone registration flow | SMS provider, sender/region setup, real OTP test |
| First admin | Private allowlist and protected review RPC | A trusted administrator must register and be added to the allowlist |
| Online payment | Razorpay test adapter and server verification | Test merchant credentials required; live payments disabled |
| Automatic payouts | Settlement eligibility ledger only | No transfers; live money movement is not enabled |
| WhatsApp | Consent + approved-template adapter | Meta Business sender, approved templates and access token required |
| Logistics | Shiprocket adapter and shipment schema | API account and pickup setup; live booking disabled |
| Tamil | Shared UI translation dictionary | Human translation/review of every page and device-sized layout |
| Price guidance | Dated daily mandi import and display | data.gov.in API key and imported observations |
| AI demand | AgriNex order-history signals | No external AI model / city-wide demand source connected |
| Farmer pilot | Field-pilot plan and metrics | Real farmers, buyers, consent and an adult/college supervisor required |

## Architecture

- **Frontend:** existing single-page HTML/CSS/JavaScript application.
- **API:** FastAPI in `index.py`.
- **Database/Auth/Storage:** dedicated Supabase project; database changes are versioned in `supabase/migrations/`.
- **Hosting:** Vercel Preview deployment.
- **Security:** user access tokens are forwarded to Supabase; database Row Level Security enforces ownership. Server-only provider calls use environment variables. A service-role key must never be sent to a browser or committed to Git.

## Required environment variables

Always server-side, never in frontend JavaScript:

| Variable | Purpose |
| --- | --- |
| `SUPABASE_URL` | Dedicated AgriNex project URL |
| `SUPABASE_PUBLISHABLE_KEY` | Supabase public/publishable API key |
| `SUPABASE_SERVICE_ROLE_KEY` | Server-only webhook/consent-recipient operations; never expose it |
| `PHONE_COUNTRY_CODE` | `+91` for Indian numbers |

Optional provider variables are documented in [docs/PROVIDER_SETUP.md](docs/PROVIDER_SETUP.md). The currently deployed project has the Supabase URL/publishable key configured; optional provider credentials are not assumed to exist.

## Supabase setup

1. Use only the dedicated AgriNex Supabase project.
2. Apply the repository migrations in order for a fresh environment. For an existing environment, inspect `supabase_migrations.schema_migrations` before applying anything.
3. Enable Supabase Auth Phone and phone confirmation.
4. Configure an SMS provider in Supabase Auth and test an OTP using an account/number you control.
5. Add a trusted administrator profile UUID to `private.agrinex_admins` using the procedure in [docs/ADMIN_SETUP.md](docs/ADMIN_SETUP.md).

The first administrator cannot be invented by the application: the profile must exist after a real, verified registration.

## Important operating limits

- **Live payments are disabled.** The current Razorpay integration is test mode only; the backend rejects `RAZORPAY_MODE=live`.
- A cancelled/closed payment window is not a confirmed payment. Orders are only marked paid after server-side signature and provider-state verification.
- **Automatic farmer payouts are disabled.** The payout endpoint reports reconciliation readiness; it does not transfer money.
- COD is not collected by marking an order delivered. An authorised admin must confirm actual collection after checking an external collection record.
- **Live Shiprocket booking is disabled** in this student pilot. The code requires an explicit logistics approval flag and a configured pickup point.
- WhatsApp is off by default; messages are sent only to users who explicitly opt in and only through an approved template. A provider response does not confirm delivery.
- Price data is a daily wholesale mandi reference in INR/quintal; the frontend converts it to INR/kg for display. It is not a farm-gate offer price or a continuous live feed.
- No external LLM is connected to the Farm AI assistant. Do not present its older demo responses as factual predictions.

## Local development

```bash
python -m venv .venv

# Windows PowerShell
.venv\\Scripts\\Activate.ps1

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
uvicorn index:app --reload
```

Open `http://localhost:8000`. Keep `.env` private.

## Tests

```bash
python -m compileall -q index.py tests
python -m unittest discover -s tests -v
```

CI also runs Python compilation and the API contract tests. Tests do not replace a full two-account end-to-end test involving a configured SMS provider, a farmer approval, product upload, stock reservation, test payment, order lifecycle and COD collection reconciliation.

## Real pilot

The pilot is **not started**. Use [docs/FARMER_PILOT.md](docs/FARMER_PILOT.md) with a local FPO/cooperative or trusted farm group, a small buyer cohort, informed consent and an adult/college supervisor. Do not promise guaranteed sales, income or payouts.
