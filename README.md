# AgriNex

AgriNex is a farm-to-family marketplace prototype. This branch adds a dedicated phone/password authentication service with SMS phone verification and separate customer/farmer onboarding.

## Authentication flow

- **Registration:** A customer or farmer enters their name, Indian mobile number, password, and (for farmers) farm name.
- **Phone verification:** Supabase Auth sends a 6-digit SMS code. Registration completes only after the code is verified.
- **Sign in:** Users sign in with mobile number + password. SMS OTP is for phone confirmation during registration, not every login.
- **Farmer approval:** New farmer profiles start with `farmer_status = pending`. Farmer dashboard and inventory routes remain locked until an administrator approves the profile.
- **Customer access:** Customer accounts cannot access farmer-only pages.

## Backend scope

The FastAPI service currently implements:

- `GET /health`
- `POST /api/auth/register`
- `POST /api/auth/resend`
- `POST /api/auth/verify`
- `POST /api/auth/login`
- `GET /api/auth/me`

The existing frontend also contains integrations for marketplace products, cart, orders, checkout/payments, inventory, AI, and realtime events. Those endpoints are **not implemented by this authentication service yet**. Until a full marketplace backend is connected, these areas remain prototype/demo functionality.

## Supabase setup

Use a **new Supabase project dedicated to AgriNex**. Do not connect AgriNex to a database belonging to another application.

1. Create the dedicated project in the selected Supabase organization and region.
2. In Supabase Auth settings, enable the Phone provider and **phone confirmation**.
3. Configure an SMS provider in Supabase. SMS delivery can incur provider charges; a free database tier does not make SMS messages free.
4. Apply the SQL migration at `supabase/migrations/202611240001_agrinex_auth_profiles.sql`.
5. Copy the project URL and publishable key. Never put a service-role key in the browser or commit secrets to GitHub.

The migration creates `public.agrinex_profiles`, enables RLS, lets authenticated users read only their own profile, and creates a profile when an Auth user is registered. Farmer profiles default to pending approval. The trigger function is placed in a private schema and direct execution is revoked from API roles.

## Environment variables

Set these in Vercel Project Settings → Environment Variables for **Preview and Production**:

| Name | Value |
| --- | --- |
| `SUPABASE_URL` | URL of the dedicated AgriNex Supabase project |
| `SUPABASE_PUBLISHABLE_KEY` | Its publishable API key |
| `PHONE_COUNTRY_CODE` | `+91` for 10-digit Indian mobile numbers |

The API accepts `SUPABASE_ANON_KEY` as a backward-compatible alternative to `SUPABASE_PUBLISHABLE_KEY`. This service does not require a service-role key.

For local development, copy `.env.example` to `.env`, install requirements, and run:

```bash
python -m venv .venv

# Windows PowerShell
.venv\Scripts\Activate.ps1

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
uvicorn index:app --reload
```

Open `http://localhost:8000`. FastAPI serves the existing `index.html` at `/` and the authentication API on the same origin. Keep `.env` private.

## Vercel deployment

The root-level `index.py` exports the FastAPI object as `app`. The Vercel project is configured with the FastAPI framework preset and the root directory. The `pyproject.toml` file also declares the entrypoint.

The Preview deployment has been verified to serve the marketplace and `/health`. The preview remains protected by Vercel Deployment Protection; do not disable protection simply to make testing easier.

The live auth service reports `authentication_configured: false` until the dedicated Supabase URL and publishable key are supplied as Vercel environment variables.

## Farmer approval

A farmer can sign in after confirming their phone, but selling tools remain blocked while the profile is pending. An administrator must verify the farmer and update the matching profile row to `approved` through a trusted administrative process. Do not add a public client-side update policy for this column.

## Tests

Run all authentication tests with:

```bash
python -m unittest discover -s tests -v
```

The suite covers phone normalization, health/API response contracts, unauthenticated access to `/api/auth/me`, and the hidden OTP form markup.

Relevant documentation:

- [Supabase phone login and SMS configuration](https://supabase.com/docs/guides/auth/phone-login)
- [Vercel Python runtime](https://vercel.com/docs/functions/runtimes/python)
- [Vercel FastAPI deployment guide](https://vercel.com/kb/guide/ship-a-fastapi-app-on-vercel)
