# AgriNex administrator setup

Farmer approval is protected by a private database allowlist. A customer or farmer cannot make themselves an administrator through the website.

## Add the first project administrator

1. Create a separate AgriNex account for the project administrator.
2. Complete phone verification for that account.
3. In Supabase Dashboard → SQL Editor, find the profile row using the administrator's verified phone number:

```sql
select id, phone, full_name, role
from public.agrinex_profiles
where phone = '+91XXXXXXXXXX';
```

4. Confirm the returned row belongs to the project owner or trusted administrator. Prefer a customer-role account rather than the farmer account that will be subject to review.
5. Add the exact UUID to the private allowlist:

```sql
insert into private.agrinex_admins (user_id)
values ('REPLACE-WITH-ADMIN-PROFILE-UUID')
on conflict (user_id) do nothing;
```

6. Sign in to the AgriNex website with that account and open the Admin console from the footer.
7. Review applications using the farmer name, farm details, pincode and contact number. Approve only after the project has checked the farmer's identity and farm information through its chosen offline/official verification process.

The application prevents an administrator from reviewing their own farmer profile. Keep allowlist membership limited to trusted maintainers. Remove access by deleting the matching row from `private.agrinex_admins`.

## Farmer approval states

- `pending`: account exists; farmer can update farm details and save draft listings, but cannot publish or process marketplace orders.
- `approved`: farmer can publish listings, submit bulk offers and process buyer orders.
- `rejected`: listings remain unavailable; the farmer can update details and request another review.

Approval state and product visibility are enforced by database policies and triggers, not a client-side toggle.

## Important operational limits

- Do not place Supabase service-role keys in the browser, environment variables exposed to the client, or GitHub.
- This admin workflow is not a legal KYC provider. The platform team must decide what documents or checks are appropriate and store only the minimum information needed.
- COD collection is not confirmed by farmer approval or by marking an order delivered. Payment reconciliation is a separate manual process in this pilot.
