-- Keep offer updates column-scoped and collapse farmer/buyer update checks into one policy.
drop policy if exists "Farmers can withdraw their own pending offers" on public.agrinex_bulk_offers;
drop policy if exists "Buyers can decide offers on their requests" on public.agrinex_bulk_offers;
drop policy if exists "Offer parties can update permitted offer states" on public.agrinex_bulk_offers;
revoke update on table public.agrinex_bulk_offers from authenticated;
grant update(status) on table public.agrinex_bulk_offers to authenticated;
create policy "Offer parties can update permitted offer states"
on public.agrinex_bulk_offers for update to authenticated
using (
 (farmer_id=(select auth.uid()) and status='pending'
  and exists(select 1 from public.agrinex_profiles p where p.id=(select auth.uid()) and p.role='farmer' and p.farmer_status='approved'))
 or exists(select 1 from public.agrinex_bulk_requests r where r.id=request_id and r.buyer_id=(select auth.uid()) and status='pending')
)
with check (
 (farmer_id=(select auth.uid()) and status='withdrawn')
 or (status in ('accepted','rejected') and exists(
   select 1 from public.agrinex_bulk_requests r where r.id=request_id and r.buyer_id=(select auth.uid())
 ))
);
