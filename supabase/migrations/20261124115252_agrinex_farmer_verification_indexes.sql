-- Farmer verification state, listing visibility and supporting indexes.
alter table public.agrinex_products add column if not exists farmer_verified boolean not null default false;
update public.agrinex_products p
set farmer_verified=coalesce((select ap.farmer_status='approved' from public.agrinex_profiles ap where ap.id=p.farmer_id),false);

create or replace function private.sync_agrinex_farmer_verification()
returns trigger language plpgsql security definer set search_path='' as $$
begin
 if old.farmer_status is distinct from new.farmer_status then
  update public.agrinex_products
  set farmer_verified=(new.farmer_status='approved'),
      status=case when new.farmer_status='approved' then status when status='active' then 'paused' else status end,
      updated_at=now()
  where farmer_id=new.id;
 end if;
 return new;
end;
$$;
revoke all on function private.sync_agrinex_farmer_verification() from public,anon,authenticated;
drop trigger if exists sync_agrinex_farmer_verification on public.agrinex_profiles;
create trigger sync_agrinex_farmer_verification after update of farmer_status on public.agrinex_profiles
for each row execute function private.sync_agrinex_farmer_verification();

create index if not exists agrinex_bulk_offers_farmer_idx on public.agrinex_bulk_offers(farmer_id,created_at desc);
create index if not exists agrinex_bulk_offers_product_idx on public.agrinex_bulk_offers(product_id);
create index if not exists agrinex_bulk_requests_buyer_idx on public.agrinex_bulk_requests(buyer_id,created_at desc);
create index if not exists agrinex_cart_items_product_idx on public.agrinex_cart_items(product_id);
create index if not exists agrinex_order_items_product_idx on public.agrinex_order_items(product_id);
