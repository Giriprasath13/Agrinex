# AgriNex provider setup and acceptance checklist

**This is a student project preview, not an activated live-commerce service.** Live payments, payouts and carrier bookings are deliberately disabled. Provider accounts, legal/merchant obligations and any live operational approval must be owned and supervised by a responsible adult or registered organisation. Do not put provider passwords, OTPs, API secrets, bank details or UPI PINs in GitHub or ChatGPT.

## Server-only environment variables

Set variables in the Vercel project environment, not in `index.html`, localStorage or browser-visible build variables.

| Variable | Used for | State in this branch |
| --- | --- | --- |
| `SUPABASE_URL` | Dedicated AgriNex database | Configured |
| `SUPABASE_PUBLISHABLE_KEY` | Supabase Data API key | Configured |
| `SUPABASE_SERVICE_ROLE_KEY` | Server-only webhook and opted-in recipient operations | Not configured |
| `PHONE_COUNTRY_CODE` | Indian phone normalization | `+91` |
| `SUPABASE_PHONE_SMS_CONFIGURED` | Operational status flag after a real OTP acceptance test | Not enabled |
| `RAZORPAY_MODE` | Gateway mode | Must be `test`; live mode is rejected by the code |
| `RAZORPAY_KEY_ID` | Razorpay test public key ID | Not configured |
| `RAZORPAY_KEY_SECRET` | Razorpay server secret | Not configured |
| `RAZORPAY_WEBHOOK_SECRET` | Webhook HMAC secret | Not configured |
| `WHATSAPP_ACCESS_TOKEN` | Meta Cloud API server token | Not configured |
| `WHATSAPP_PHONE_NUMBER_ID` | WhatsApp Business sender ID | Not configured |
| `WHATSAPP_GRAPH_API_VERSION` | Current supported Graph API version | Not configured |
| `WHATSAPP_ORDER_TEMPLATE_NAME` | Approved template with two body placeholders: order number and status | Not configured |
| `WHATSAPP_TEMPLATE_LANGUAGE_EN` | Approved English template language code | Not configured |
| `WHATSAPP_TEMPLATE_LANGUAGE_TA` | Approved Tamil template language code | Not configured |
| `SHIPROCKET_API_EMAIL` / `SHIPROCKET_API_PASSWORD` | Shiprocket API user | Not configured |
| `AGRI_LIVE_LOGISTICS_APPROVED` | Additional adult/operator gate | Must remain unset for this student pilot |
| `SHIPROCKET_PICKUP_LOCATION` | Fallback registered pickup point | Not configured |
| `DATA_GOV_IN_API_KEY` | Daily AGMARKNET/data.gov.in import | Not configured |
| `DATA_GOV_IN_RESOURCE_ID` | Optional override for the daily mandi resource | Defaults to `9ef84268-d588-465a-a308-a864a43d0070` |

Never set `SUPABASE_SERVICE_ROLE_KEY` in a `NEXT_PUBLIC_` / frontend variable or commit it to the repository. The backend needs this key for a few server-only functions; it is not a user-facing key.

## 1. SMS phone confirmation

1. In the dedicated Supabase project, enable the Auth Phone provider and phone confirmation.
2. Configure a supported SMS provider in the Supabase Auth dashboard, with its sender and allowed destination settings.
3. Review provider rate limits, SMS charges, country permissions and OTP expiry settings.
4. Use consenting test numbers controlled by the project supervisor to test registration, resend and verification.
5. Confirm that an unverified account cannot reach the authenticated marketplace routes.
6. Only after the end-to-end test succeeds should the operational flag `SUPABASE_PHONE_SMS_CONFIGURED=true` be set in Vercel.

The flag does not configure or send SMS itself; it reports that the manual Supabase Auth test has been completed.

## 2. First administrator

There is no hard-coded admin user and no admin-demo shortcut. A real, verified Supabase profile must exist before it can be allowlisted.

A trusted adult project maintainer should:
1. Register a separate administrator account through the normal AgriNex phone-confirmation flow.
2. Verify the account and confirm its profile row exists.
3. In the Supabase SQL editor, check the profile UUID:
   ```sql
   select id, phone, full_name, role
   from public.agrinex_profiles
   where phone = '+91XXXXXXXXXX';
   ```
4. Add the reviewed profile UUID:
   ```sql
   insert into private.agrinex_admins(user_id)
   values ('REPLACE-WITH-VERIFIED-ADMIN-UUID')
   on conflict (user_id) do nothing;
   ```
5. Sign in with that account and open the Admin console. The server checks the allowlist for every protected action.

Do not use an unverified account, a guessed UUID or a farmer account as the only administrator. The database blocks an admin from reviewing their own farmer application.

## 3. Online payment test adapter

The code accepts **Razorpay test mode only**. It rejects `RAZORPAY_MODE=live`. It requires the test key ID, key secret, webhook secret and server-only Supabase service-role key before the checkout option is displayed.

Before any sandbox test:
- A responsible adult/project supervisor must control the test merchant account and server secrets.
- Configure the provider's test webhook to `https://YOUR_PUBLIC_API_HOST/api/webhooks/razorpay` for `payment.captured` and `payment.failed`.
- Ensure the webhook host is publicly reachable by the provider; a Vercel preview protected by Vercel Authentication will not receive unauthenticated provider callbacks.
- Verify the checkout signature, fetch the provider payment, compare INR amount and order ID, and verify webhook idempotency.
- Test success, failed, cancelled and delayed webhook cases. A browser callback alone does not make an order paid.

**Live payments are not enabled.** Do not change the code gate or enable real-money collection from this student project.

## 4. WhatsApp order messages

1. A responsible organisation must own the WhatsApp Business sender and provider access.
2. Configure an approved template with exactly two body parameters: AgriNex order number and status.
3. Supply the approved language codes and the server-only token/sender/version variables.
4. Each customer or farmer must opt in on the Orders page before their number can be targeted.
5. Verify opt-out removes the stored messaging number and prevents later messages.
6. Test delivery receipts before treating messages as delivered.

The adapter reports provider acceptance, not delivery confirmation. Delivery-status webhook reconciliation is not implemented yet. WhatsApp is off by default.

## 5. Logistics and pickup

A Shiprocket adapter is present for courier serviceability, parcel dimensions, booking, AWB assignment and pickup requests. **Live booking is blocked** by default in this student pilot. It requires an adult-operated organisation to own the provider account, a registered pickup point, a real delivery email/contact and approved operational permission. Do not set `AGRI_LIVE_LOGISTICS_APPROVED=true` for this student build.

Automated shipment-status webhook reconciliation is not yet implemented. The current adapter must not be represented as continuous live tracking.

## 6. Daily mandi price observations

The admin page has a sync action for the daily AGMARKNET/data.gov.in resource. A responsible admin must configure `DATA_GOV_IN_API_KEY` in Vercel and run a narrow state/district/commodity sync.

Every imported row stores its source and arrival date. The UI labels wholesale observations in INR/quintal and converts to INR/kg for display. These records are not guaranteed farm-gate prices, not continuous real-time data, and not a forecast. The source can have missing or delayed market reporting.

## 7. Payouts and COD reconciliation

Automatic farmer transfers are **not implemented or enabled**. The current endpoint calculates settlement readiness only after:
- the order item is delivered; and
- Razorpay is captured or COD collection is manually confirmed by an allowlisted administrator.

The COD action records an operator-confirmed collection; it does not collect or transfer money. No bank credentials or UPI PINs are collected by AgriNex.

## 8. Tamil, AI and real pilot

- The shared UI has a broad Tamil/English translation dictionary. It still needs a native Tamil reviewer to check all customer/farmer flows, errors, dates and responsive layouts.
- The Farm Data Assistant reports live catalogue values only. It does not call an LLM. Weather, crop prediction and city-wide demand AI are not connected.
- The mandi-data view and AgriNex order-history summaries are separate evidence sources; internal orders must not be marketed as total local demand.
- The farmer pilot has not started. Use the field plan in `docs/FARMER_PILOT.md`, with a trusted local farmer organisation, consenting participants, an adult/college supervisor and recorded acceptance tests.

## Final release gate

Do not mark this application production-ready until the responsible operator has completed SMS verification, admin access, gateway sandbox tests, WhatsApp opt-in/delivery tests, carrier test shipment, data freshness checks, Tamil user review, farmer/buyer acceptance tests, privacy review and a written pilot approval. Live financial or logistics operations are outside this student preview.
