# AgriNex

AgriNex is a farmer-first, farm-to-family marketplace. The current feature branch upgrades the original single-file frontend prototype with a FastAPI API layer and a dedicated Supabase database for shared farmer, product, order and buyer data.

> **Deployment scope:** changes are being developed on `feature/phone-sms-auth`. They are not merged into `main`. Treat the preview as a pilot build, not a production marketplace until the external setup and real-user acceptance checks below are complete.

## Product workflows

### Customers and business buyers

- Customer registration and sign-in use phone number + password.
- Registration requires a six-digit SMS phone confirmation.
- Discover active listings from approved farmers.
- Save delivery addresses and maintain a server-backed cart after sign-in.
- Place marketplace orders using **Cash on Delivery only**.
- The database checks the current listing, farmer approval and stock inside one transaction before confirming an order.
- Business buyers can post bulk requirements. Approved farmers can submit quotations against their own active listings. Accepting an offer is an agreement step; it does not automatically create an order.

### Farmers

- Farmer registration begins with a pending verification status.
- Farm details can be submitted for administrator review.
- Pending farmers can view their review status and save draft listings, but cannot publish or process orders.
- Approved farmers can create listings, upload product photos, edit stock, manage orders and submit bulk offers.
- The dashboard uses API-backed product, order and delivered-sales summaries rather than the old fixed demo revenue and order values.
- Product images accept JPEG, PNG or WEBP files up to 5 MB.
- Browser-supported speech recognition can fill the product name in English (India) or Tamil (India). This is speech-to-text assistance, not an AI crop-recognition system.
- A text-only offline draft queue is available in the browser. Photo files are not saved to that queue; the farmer must select the photo again after reconnecting.

### Farmer discovery and trust

- Public listings are visible only while the product is active and its farmer profile is approved.
- Farmer identity and farm-name display values are tied to the authenticated profile in the database.
- Approval changes update the listing visibility state.
- Product detail pages include product guidance, freshness/storage suggestions, related listings and customer reviews.

## API endpoints

Authentication:
- `GET /health`
- `POST /api/auth/register`
- `POST /api/auth/resend`
- `POST /api/auth/verify`
- `POST /api/auth/login`
- `GET /api/auth/me`

Profiles and farmer verification:
- `GET/PATCH /api/profile`
- `POST /api/farmer/verification`
- `GET /api/admin/farmer-applications`
- `POST /api/admin/farmer-applications/{farmer_id}/review`

Products, storage and inventory:
- `GET /api/products`
- `GET /api/farmer/products`
- `POST /api/products`
- `PATCH/DELETE /api/products/{product_id}`
- `POST /api/products/{product_id}/image`

Cart, addresses and orders:
- `GET/POST/DELETE /api/cart`
- `PATCH /api/cart/{product_id}`
- `GET/POST /api/addresses`
- `GET/POST /api/orders`
- `POST /api/orders/{order_id}/status`
- `GET /api/farmer/orders`
- `GET /api/farmer/summary`
- `GET /api/farmer/earnings`
- `GET/PATCH /api/notifications`

Bulk procurement:
- `GET/POST /api/bulk-requests`
- `GET /api/bulk-requests/{request_id}/offers`
- `POST /api/bulk-requests/{request_id}/offers`
- `PATCH /api/bulk-offers/{offer_id}`

Health:
- `GET /api/marketplace/health`

The FastAPI service forwards the signed-in user's Supabase access token. It uses the Supabase publishable key on the server and does **not** need a service-role key. Row Level Security and database functions enforce ownership, farmer approval and order transitions.

## Supabase setup

Use a dedicated Supabase project for AgriNex. Do not connect this app to a database belonging to another application.

Repository migrations are in `supabase/migrations/`. They describe the auth profile, marketplace, farmer-verification, order, bulk-offer and earnings schemas. The hosted AgriNex project already has these migrations applied; do not rerun SQL manually against an existing environment without first checking its migration history.

In Supabase Auth settings:
1. Enable the Phone provider and phone confirmation.
2. Configure an SMS provider and verify its sender/region settings.
3. Check destination country permissions, OTP expiry, rate limits and provider balance.
4. Test registration and SMS delivery using a consenting test account.

SMS provider costs are separate from the Supabase database plan.

### Administrator allowlist

Farmer approvals require a user ID in `private.agrinex_admins`. There is no browser-side admin switch. Create a dedicated customer account for the project administrator, verify that account, then obtain its profile UUID in the SQL editor:

```sql
select id, phone, full_name, role
from public.agrinex_profiles
where phone = '+91XXXXXXXXXX';
```

Add the verified administrator account UUID to the private allowlist:

```sql
insert into private.agrinex_admins(user_id)
values ('REPLACE-WITH-ADMIN-PROFILE-UUID')
on conflict (user_id) do nothing;
```

Do not use a farmer's own account as the only administrator: the system prevents an administrator from reviewing their own farmer application. Keep administrator membership limited to trusted project maintainers.

## Environment variables

Set these in Vercel Project Settings → Environment Variables for Preview and Production:

| Name | Value |
| --- | --- |
| `SUPABASE_URL` | URL of the dedicated AgriNex Supabase project |
| `SUPABASE_PUBLISHABLE_KEY` | Its publishable API key |
| `PHONE_COUNTRY_CODE` | `+91` for ten-digit Indian mobile numbers |

The API accepts `SUPABASE_ANON_KEY` as a backward-compatible alternative. Never add a service-role key to frontend code, public environment variables or GitHub.

## Payment, payout and delivery limits

- Checkout currently supports **Cash on Delivery only**.
- The COD collection state remains pending until it is confirmed through a separate process. The app does not collect money.
- Farmer earnings are computed only from delivered order items. The displayed payout is an estimate, not a bank or UPI settlement.
- Platform fee and delivery fee are currently zero in the order function. Publish a real fee schedule before charging farmers or buyers.
- Delivery statuses can be advanced by the farmer. There is no live carrier allocation, route tracking or automated pickup service yet.
- Bulk offers are quotations. A separate confirmed order is still needed after quantity, quality and delivery are agreed.

## Language, voice and AI

- The farmer dashboard has a Tamil/English preference and a partial Tamil translation dictionary. The complete customer marketplace and every form are not yet fully translated.
- Voice selling relies on browser Speech Recognition availability and device language support. Manual entry is always available.
- Product-photo upload stores the image; it does not identify crops or assess quality.
- The existing Farm AI assistant still uses demo responses unless a secure backend AI endpoint is configured. Do not treat demo demand or price suggestions as market facts.

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

Run the API and phone tests:

```bash
python -m compileall -q index.py tests
python -m unittest discover -s tests -v
```

The current tests cover API contracts, unauthenticated access, phone normalization and required frontend surfaces. They do not yet replace full end-to-end testing with two real accounts, SMS provider delivery, farmer approval, product uploads, order lifecycle and COD collection.

## Pilot approach

Start with one local farmer organisation or FPO, a small produce cluster, a few household buyers and a few business buyers. Track completed transactions, repeat purchases, farmer earnings and order fulfilment. See [docs/FARMER_PILOT.md](docs/FARMER_PILOT.md).

Relevant setup references:
- [Supabase phone login and SMS configuration](https://supabase.com/docs/guides/auth/phone-login)
- [Vercel Python runtime](https://vercel.com/docs/functions/runtimes/python)
- [Vercel FastAPI deployment guide](https://vercel.com/kb/guide/ship-a-fastapi-app-on-vercel)
