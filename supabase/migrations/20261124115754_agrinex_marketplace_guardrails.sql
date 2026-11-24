-- Marketplace ownership, approval and state-transition guardrails.
drop policy if exists "Public and farmers can read AgriNex products" on public.agrinex_products;
create policy "Public and farmers can read AgriNex products"
on public.agrinex_products for select to anon,authenticated
using ((status='active' and farmer_verified=true) or farmer_id=(select auth.uid()));

create or replace function private.enforce_agrinex_product_owner_fields()
returns trigger language plpgsql security definer set search_path='' as $$
declare v_profile public.agrinex_profiles%rowtype;
begin
 if auth.uid() is null or auth.uid()<>new.farmer_id then
  raise exception 'Product ownership cannot be changed.' using errcode='42501';
 end if;
 select * into v_profile from public.agrinex_profiles where id=new.farmer_id and role='farmer';
 if not found then raise exception 'A valid farmer profile is required.' using errcode='42501'; end if;
 new.farmer_name:=v_profile.full_name;
 new.farmer_farm_name:=coalesce(v_profile.farm_name,'Farm');
 new.farmer_verified:=(v_profile.farmer_status='approved');
 if v_profile.farmer_status<>'approved' and new.status='active' then new.status:='draft'; end if;
 new.updated_at:=now();
 return new;
end;
$$;
revoke all on function private.enforce_agrinex_product_owner_fields() from public,anon,authenticated;
drop trigger if exists enforce_agrinex_product_owner_fields on public.agrinex_products;
create trigger enforce_agrinex_product_owner_fields before insert or update on public.agrinex_products
for each row execute function private.enforce_agrinex_product_owner_fields();

create or replace function private.touch_agrinex_updated_at()
returns trigger language plpgsql set search_path='' as $$
begin new.updated_at:=now(); return new; end;
$$;
revoke all on function private.touch_agrinex_updated_at() from public,anon,authenticated;
drop trigger if exists agrinex_profiles_updated_at on public.agrinex_profiles;
create trigger agrinex_profiles_updated_at before update on public.agrinex_profiles for each row execute function private.touch_agrinex_updated_at();
drop trigger if exists agrinex_products_updated_at on public.agrinex_products;
create trigger agrinex_products_updated_at before update on public.agrinex_products for each row execute function private.touch_agrinex_updated_at();
drop trigger if exists agrinex_bulk_requests_updated_at on public.agrinex_bulk_requests;
create trigger agrinex_bulk_requests_updated_at before update on public.agrinex_bulk_requests for each row execute function private.touch_agrinex_updated_at();

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

-- Replace order transition function with the verified-farmer gate.
create or replace function public.update_agrinex_order_status(p_order_id uuid,p_status text)
returns jsonb language plpgsql security definer set search_path='' as $$
declare
 v_user uuid:=auth.uid();
 v_order public.agrinex_orders%rowtype;
 v_is_farmer boolean;
 v_farmer_status text;
begin
 if v_user is null then raise exception 'Please sign in.' using errcode='42501'; end if;
 select * into v_order from public.agrinex_orders where id=p_order_id for update;
 if not found then raise exception 'Order not found.' using errcode='P0002'; end if;
 v_is_farmer:=v_order.farmer_id=v_user;
 if v_order.buyer_id<>v_user and not v_is_farmer then raise exception 'You are not allowed to update this order.' using errcode='42501'; end if;
 if v_is_farmer then
  select farmer_status into v_farmer_status from public.agrinex_profiles where id=v_user and role='farmer';
  if v_farmer_status is distinct from 'approved' then raise exception 'Only approved farmers can update marketplace orders.' using errcode='42501'; end if;
 end if;
 if v_order.buyer_id=v_user then
  if p_status<>'cancelled' or v_order.status not in ('pending_acceptance','confirmed') then raise exception 'Customers can cancel only orders that have not been prepared.' using errcode='42501'; end if;
 else
  if p_status not in ('confirmed','preparing','ready_for_pickup','picked_up','out_for_delivery','delivered','cancelled') then raise exception 'Invalid farmer order status.' using errcode='22023'; end if;
  if p_status='cancelled' and v_order.status not in ('pending_acceptance','confirmed') then raise exception 'This order can no longer be cancelled by the farmer.' using errcode='42501'; end if;
  if p_status='confirmed' and v_order.status<>'pending_acceptance' then raise exception 'Only new orders can be accepted.' using errcode='42501'; end if;
  if p_status='preparing' and v_order.status<>'confirmed' then raise exception 'Accept the order before preparing it.' using errcode='42501'; end if;
  if p_status='ready_for_pickup' and v_order.status<>'preparing' then raise exception 'Mark the order as preparing before it is ready.' using errcode='42501'; end if;
  if p_status='picked_up' and v_order.status<>'ready_for_pickup' then raise exception 'The order must be ready before pickup.' using errcode='42501'; end if;
  if p_status='out_for_delivery' and v_order.status not in ('ready_for_pickup','picked_up') then raise exception 'The order must be ready for delivery first.' using errcode='42501'; end if;
  if p_status='delivered' and v_order.status not in ('picked_up','out_for_delivery') then raise exception 'The order must be picked up before delivery confirmation.' using errcode='42501'; end if;
 end if;
 if p_status='cancelled' then
  update public.agrinex_products p set stock=p.stock+i.quantity from public.agrinex_order_items i where i.order_id=p_order_id and i.product_id=p.id;
  update public.agrinex_order_items set status='cancelled',updated_at=now() where order_id=p_order_id;
 else
  update public.agrinex_order_items set status=p_status,updated_at=now() where order_id=p_order_id;
 end if;
 update public.agrinex_orders set status=p_status,updated_at=now() where id=p_order_id;
 insert into public.agrinex_notifications(user_id,type,title,message)
 values(case when v_is_farmer then v_order.buyer_id else v_order.farmer_id end,'order','Order status updated',
        'Order '||v_order.order_number||' is now '||replace(p_status,'_',' ')||'.');
 return jsonb_build_object('id',p_order_id,'order_number',v_order.order_number,'status',p_status);
end;
$$;
revoke all on function public.update_agrinex_order_status(uuid,text) from public,anon;
grant execute on function public.update_agrinex_order_status(uuid,text) to authenticated;
