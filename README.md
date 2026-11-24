# AgriNex

AgriNex is a farm-to-family marketplace prototype. The current branch adds a dedicated phone/password authentication service with SMS phone verification and separate customer/farmer onboarding.

## Authentication flow

- **Registration:** customer or farmer enters their name, Indian mobile number, password, and (for farmers) farm name.
- **Phone verification:** Supabase Auth sends a 6-digit SMS code. Registration is not considered complete until the code is verified.
- **Sign in:** users continue to sign in with mobile number + password. SMS OTP is for phone confirmation, not every login.
- **Farmer approval:** new farmer profiles start with \`farmer_status = pending\`. Farmer dashboard and inventory routes remain locked until an administrator approves the profile.
- **Customer access:** customers receive the customer role and cannot enter farmer-only pages.

## Backend scope

The FastAPI service currently implements:

- \`GET /health\`
- \`POST /api/auth/register\`
- \`POST /api/auth/resend\`
- \`POST /api/auth/verify\`
- \`POST /api/auth/login\`
- \`GET /api/auth/me\`

The existing frontend contains additional marketplace, cart, order, payment, inventory, AI, and realtime API integrations. Those endpoints are **not implemented by this authentication service yet**; until they are connected to a full application backend, those parts of the frontend remain prototype/demo functionality. Do not describe the whole marketplace backend as production-ready.

## Supabase setup

Use a **new Supabase project dedicated to AgriNex**. Do not connect AgriNex to a database belonging to another application.

1. Create the dedicated project in the intended Supabase organization and region.
2. In Supabase Auth settings, enable the Phone provider and **phone confirmation**.
3. Configure an SMS provider in Supabase. SMS delivery can incur provider charges; a free database tier does not make SMS messages free.
4. Open the Supabase SQL Editor and run the SQL in \`supabase/migrations/202611240001_agrinex_auth_profiles.sql\`.
5. Copy the project URL and its publishable key. Do not use a service-role key in the browser or commit any secret to GitHub.

The migration creates \`public.agrinex_profiles\`, enables RLS, allows authenticated users to read only their own profile, and inserts a profile when an Auth user is created. Farmer profiles default to pending approval.

## Environment variables

Set these in Vercel Project Settings → Environment Variables (Production and Preview):

| Name | Value |
| --- | --- |
| \`SUPABASE_URL\` | URL of the dedicated AgriNex Supabase project |
| \`SUPABASE_PUBLISHABLE_KEY\` | Its publishable API key |
| \`PHONE_COUNTRY_CODE\` | \`+91\` for 10-digit Indian mobile numbers |

The API reads \`SUPABASE_ANON_KEY\` as a backward-compatible alternative to \`SUPABASE_PUBLISHABLE_KEY\`. Never set \`SUPABASE_SERVICE_ROLE_KEY\` in the frontend; this authentication API does not need it.

For local development, copy \`.env.example\` to \`.env\`, install requirements, and run:

\`\`\`bash
python -m venv .venv
# Windows PowerShell:
.venv\\Scripts\\Activate.ps1
# macOS / Linux:
source .venv/bin/activate

pip install -r requirements.txt
uvicorn index:app --reload
\`\`\`

Open \`http://localhost:8000\`. The FastAPI app serves the existing \`index.html\` at \`/\` and the auth endpoints on the same origin. Keep \`.env\` private and out of Git.

## Vercel deployment

The project uses a root-level \`index.py\` FastAPI entry point and \`requirements.txt\`. Vercel's Python/FastAPI runtime recognizes root-level FastAPI entry points. Keep the project root as the Vercel Root Directory. Set the environment variables above and deploy a Preview first; promote to Production only after the preview and auth configuration have been tested.

The current project has Vercel Deployment Protection enabled for preview deployments. Use an authorized preview access method for testing; do not disable protection just to make the preview public.

## Farmer approval

A farmer can sign in after confirming their phone, but selling tools remain blocked while the profile is pending. An administrator must verify the farmer and update the matching profile row to \`approved\` through a trusted administrative process. Do not add a public client-side update policy for this column.

## Tests

Run the phone-normalization unit tests with:

\`\`\`bash
python -m unittest discover -s tests -v
\`\`\`

Relevant documentation:

- [Supabase phone login and SMS configuration](https://supabase.com/docs/guides/auth/phone-login)
- [Vercel Python runtime](https://vercel.com/docs/functions/runtimes/python)
- [Vercel FastAPI deployment overview](https://vercel.com/i/what-is-fastapi)
