# AgriNex SMS authentication setup

This guide completes the external configuration for the phone + password registration flow.

## Current implementation

- Customer and Farmer have separate registration and sign-in screens.
- Registration uses phone + password and requests an SMS OTP.
- Sign-in uses phone + password after phone confirmation.
- Indian 10-digit phone numbers are normalized to E.164 using `PHONE_COUNTRY_CODE=+91`.
- Farmer profiles are created with `farmer_status=pending`.
- The backend reads the signed-in user's own profile using the user's access token and database RLS.
- The server uses only the Supabase publishable key for Auth API calls; do not put a Supabase secret/service-role key in browser code.

## Supabase dashboard configuration

Open the AgriNex project: https://supabase.com/dashboard/project/ybfhsgofurowfuxmtvbt

1. Open **Authentication → Providers**.
2. Open the **Phone** provider and enable phone authentication.
3. Enable phone confirmation so signup does not immediately create an authenticated session before OTP verification.
4. Configure a supported SMS provider and enter its credentials in the Supabase dashboard. Save the settings.
5. Check the SMS provider's sender ID, destination-country permissions, and account balance/limits. SMS delivery can incur charges.
6. Keep OTP expiry and rate limits enabled. Do not disable abuse protections for testing.

Supabase documentation:
- https://supabase.com/docs/guides/auth/phone-login
- https://supabase.com/docs/guides/auth/passwords

## Vercel environment variables

The Vercel project `agrinex` already has these variables configured for Preview and Production:

- `SUPABASE_URL`
- `SUPABASE_PUBLISHABLE_KEY`
- `PHONE_COUNTRY_CODE=+91`

Do not add SMS-provider credentials to Vercel for the native Supabase SMS provider. Configure those in Supabase Auth settings.

## Safe end-to-end test

After configuring the provider:

1. Open the AgriNex preview.
2. Register a test Customer using a phone number you control and a unique password.
3. Confirm the SMS arrives, enter the six-digit OTP, and verify that the Customer session opens.
4. Sign out and sign in again using the same phone number and password.
5. Repeat using a test Farmer account and a farm name.
6. Confirm the Farmer account is marked **Pending approval** and cannot access selling tools before approval.
7. Check Supabase **Authentication → Users** and the `public.agrinex_profiles` table for the verified test accounts.
8. Delete test accounts after testing if they are no longer needed.

Never commit provider credentials, passwords, OTPs, or Supabase secret/service-role keys to GitHub. Do not send them in chat.
