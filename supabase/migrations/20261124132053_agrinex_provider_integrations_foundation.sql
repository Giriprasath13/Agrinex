-- AgriNex provider integration foundation.
-- Secrets remain server-side. This migration stores only provider references and audit data.

alter table public.agrinex_profiles
  add column if not exists whatsapp_phone text,
  add column if not exists whatsapp_opt_in boolean not null default false,
  add column if not exists whatsapp_opt_in_at timestamptz,
  add column if not exists whatsapp_language text not null default 'en',
  add column if not exists razorpay_route_account_id text,
  add column if not exists shiprocket_pickup_location text;

grant update (whatsapp_phone, whatsapp_opt_in, whatsapp_opt_in_at, whatsapp_language)
on public.agrinex_profiles to authenticated;

alter table public.agrinex_orders
  drop constraint if exists agrinex_orders_payment_method_check;
alter table public.agrinex_orders
  add constraint agrinex_orders_payment_method_check
  check (payment_method in ('cash_on_delivery','razorpay'));

alter table public.agrinex_orders
  drop constraint if exists agrinex_orders_payment_status_check;
alter table public.agrinex_orders
  add constraint agrinex_orders_payment_status_check
  check (payment_status in (
    'pending_collection','awaiting_payment','authorized','captured',
    'collected','failed','refund_pending','refunded','not_applicable'
  ));

alter table public.agrinex_orders
  add column if not exists payment_provider text,
  add column if not exists provider_order_id text,
  add column if not exists provider_payment_id text,
  add column if not exists payment_verified_at timestamptz;

create unique index if not exists agrinex_orders_provider_order_id_uidx
  on public.agrinex_orders(provider_order_id)
  where provider_order_id is not null;
create unique index if not exists agrinex_orders_provider_payment_id_uidx
  on public.agrinex_orders(provider_payment_id)
  where provider_payment_id is not null;

alter table public.agrinex_order_items
  drop constraint if exists agrinex_order_items_payout_status_check;
alter table public.agrinex_order_items
  add constraint agrinex_order_items_payout_status_check
  check (payout_status in (
    'not_settled','settlement_pending','processing','settled','failed','reversed'
  ));

create table if not exists public.agrinex_shipments (
  id uuid primary key default gen_random_uuid(),
  order_id uuid not null unique references public.agrinex_orders(id) on delete restrict,
  farmer_id uuid not null references public.agrinex_profiles(id) on delete restrict,
  buyer_id uuid not null references public.agrinex_profiles(id) on delete restrict,
  provider text not null check (provider in ('shiprocket','manual')),
  provider_order_id text,
  shipment_id text,
  awb_code text,
  courier_company_id text,
  courier_name text,
  tracking_url text,
  pickup_location_name text,
  pickup_pincode text not null check (pickup_pincode ~ '^[0-9]{6}$'),
  delivery_pincode text not null check (delivery_pincode ~ '^[0-9]{6}$'),
  parcel_length_cm numeric(8,2) not null check (parcel_length_cm > 0),
  parcel_breadth_cm numeric(8,2) not null check (parcel_breadth_cm > 0),
  parcel_height_cm numeric(8,2) not null check (parcel_height_cm > 0),
  parcel_weight_kg numeric(8,3) not null check (parcel_weight_kg > 0),
  status text not null default 'pending_booking'
    check (status in ('pending_booking','booked','pickup_scheduled','picked_up','in_transit','out_for_delivery','delivered','cancelled','exception')),
  last_event text,
  last_event_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists agrinex_shipments_farmer_idx on public.agrinex_shipments(farmer_id,created_at desc);
create index if not exists agrinex_shipments_buyer_idx on public.agrinex_shipments(buyer_id,created_at desc);
create index if not exists agrinex_shipments_awb_idx on public.agrinex_shipments(awb_code) where awb_code is not null;
alter table public.agrinex_shipments enable row level security;
revoke all on public.agrinex_shipments from anon,authenticated;
grant select on public.agrinex_shipments to authenticated;
drop policy if exists "Order parties can read shipment details" on public.agrinex_shipments;
create policy "Order parties can read shipment details"
on public.agrinex_shipments for select to authenticated
using (buyer_id=(select auth.uid()) or farmer_id=(select auth.uid()));

create table if not exists public.agrinex_payouts (
  id uuid primary key default gen_random_uuid(),
  order_id uuid not null references public.agrinex_orders(id) on delete restrict,
  farmer_id uuid not null references public.agrinex_profiles(id) on delete restrict,
  provider text not null check (provider in ('razorpay_route')),
  provider_transfer_id text unique,
  linked_account_id text not null,
  amount numeric(12,2) not null check (amount > 0),
  currency text not null default 'INR' check (currency='INR'),
  status text not null default 'created'
    check (status in ('created','pending','processed','reversed','failed')),
  idempotency_reference text not null unique,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(order_id,farmer_id)
);
create index if not exists agrinex_payouts_farmer_idx on public.agrinex_payouts(farmer_id,created_at desc);
alter table public.agrinex_payouts enable row level security;
revoke all on public.agrinex_payouts from anon,authenticated;
grant select on public.agrinex_payouts to authenticated;
drop policy if exists "Payout parties can read payout records" on public.agrinex_payouts;
create policy "Payout parties can read payout records"
on public.agrinex_payouts for select to authenticated
using (
  farmer_id=(select auth.uid())
  or exists(select 1 from public.agrinex_orders o where o.id=order_id and o.buyer_id=(select auth.uid()))
);

create table if not exists public.agrinex_market_price_observations (
  id uuid primary key default gen_random_uuid(),
  state text not null,
  district text not null,
  market text not null,
  commodity text not null,
  variety text,
  grade text,
  arrival_date date not null,
  min_price numeric(12,2) not null check (min_price >= 0),
  max_price numeric(12,2) not null check (max_price >= min_price),
  modal_price numeric(12,2) not null check (modal_price >= min_price and modal_price <= max_price),
  price_unit text not null default 'INR_PER_QUINTAL',
  source text not null default 'AGMARKNET_DATA_GOV_IN',
  source_url text not null,
  imported_at timestamptz not null default now(),
  unique(state,district,market,commodity,variety,arrival_date,source)
);
create index if not exists agrinex_market_prices_lookup_idx
  on public.agrinex_market_price_observations(lower(commodity),lower(district),arrival_date desc);
create index if not exists agrinex_market_prices_source_idx
  on public.agrinex_market_price_observations(source,arrival_date desc);
alter table public.agrinex_market_price_observations enable row level security;
revoke all on public.agrinex_market_price_observations from anon,authenticated;
grant select on public.agrinex_market_price_observations to anon,authenticated;
drop policy if exists "Market price observations are public read only" on public.agrinex_market_price_observations;
create policy "Market price observations are public read only"
on public.agrinex_market_price_observations for select to anon,authenticated
using (true);

create schema if not exists private;
revoke all on schema private from public,anon,authenticated;
create table if not exists private.agrinex_provider_events (
  id uuid primary key default gen_random_uuid(),
  provider text not null,
  provider_event_id text not null,
  event_type text not null,
  processed_at timestamptz not null default now(),
  unique(provider,provider_event_id)
);
revoke all on private.agrinex_provider_events from public,anon,authenticated;

create or replace function public.attach_agrinex_payment_order(
  p_order_id uuid,p_provider_order_id text
) returns jsonb
language plpgsql security definer set search_path='' as $$
declare v_order public.agrinex_orders%rowtype;
begin
  if auth.uid() is null then raise exception 'Sign in required.' using errcode='42501'; end if;
  select * into v_order from public.agrinex_orders where id=p_order_id for update;
  if not found or v_order.buyer_id<>auth.uid() then raise exception 'Order not found.' using errcode='42501'; end if;
  if v_order.payment_method<>'razorpay' or v_order.payment_status<>'awaiting_payment' then
    raise exception 'This order is not awaiting an online payment.' using errcode='22023';
  end if;
  if v_order.provider_order_id is not null and v_order.provider_order_id<>p_provider_order_id then
    raise exception 'A different payment order is already attached.' using errcode='23505';
  end if;
  update public.agrinex_orders
  set provider_order_id=p_provider_order_id,payment_provider='razorpay',updated_at=now()
  where id=p_order_id;
  return jsonb_build_object('order_id',p_order_id,'provider_order_id',p_provider_order_id);
end;
$$;
revoke all on function public.attach_agrinex_payment_order(uuid,text) from public,anon;
grant execute on function public.attach_agrinex_payment_order(uuid,text) to authenticated;

create or replace function public.get_agrinex_whatsapp_targets(p_order_id uuid)
returns table(user_id uuid,phone text,role text,language text)
language plpgsql security definer set search_path='' as $$
declare v_order public.agrinex_orders%rowtype;
begin
  if auth.uid() is null then raise exception 'Sign in required.' using errcode='42501'; end if;
  select * into v_order from public.agrinex_orders where id=p_order_id;
  if not found or (v_order.buyer_id<>auth.uid() and v_order.farmer_id<>auth.uid()) then
    raise exception 'Order not found.' using errcode='42501';
  end if;
  return query
  select p.id,coalesce(nullif(p.whatsapp_phone,''),p.phone),p.role,p.whatsapp_language
  from public.agrinex_profiles p
  where p.id in (v_order.buyer_id,v_order.farmer_id) and p.whatsapp_opt_in=true;
end;
$$;
revoke all on function public.get_agrinex_whatsapp_targets(uuid) from public,anon;
grant execute on function public.get_agrinex_whatsapp_targets(uuid) to authenticated;

create or replace function public.record_agrinex_shipment(
 p_order_id uuid,p_provider text,p_provider_order_id text,p_shipment_id text,
 p_awb_code text,p_courier_name text,p_tracking_url text,p_pickup_location_name text,
 p_pickup_pincode text,p_delivery_pincode text,p_length numeric,p_breadth numeric,
 p_height numeric,p_weight numeric,p_status text
) returns jsonb
language plpgsql security definer set search_path='' as $$
declare v_order public.agrinex_orders%rowtype; v_id uuid;
begin
 if auth.uid() is null then raise exception 'Sign in required.' using errcode='42501'; end if;
 select * into v_order from public.agrinex_orders where id=p_order_id for update;
 if not found or v_order.farmer_id<>auth.uid() then raise exception 'Order not found.' using errcode='42501'; end if;
 if not exists(select 1 from public.agrinex_profiles p where p.id=auth.uid() and p.role='farmer' and p.farmer_status='approved') then
   raise exception 'Approved farmer account required.' using errcode='42501';
 end if;
 if v_order.status not in ('confirmed','preparing','ready_for_pickup') then
   raise exception 'Shipment can be booked after the farmer accepts the order.' using errcode='22023';
 end if;
 insert into public.agrinex_shipments(
  order_id,farmer_id,buyer_id,provider,provider_order_id,shipment_id,awb_code,
  courier_name,tracking_url,pickup_location_name,pickup_pincode,delivery_pincode,
  parcel_length_cm,parcel_breadth_cm,parcel_height_cm,parcel_weight_kg,status
 ) values (
  p_order_id,v_order.farmer_id,v_order.buyer_id,p_provider,p_provider_order_id,p_shipment_id,p_awb_code,
  p_courier_name,p_tracking_url,p_pickup_location_name,p_pickup_pincode,p_delivery_pincode,
  p_length,p_breadth,p_height,p_weight,p_status
 )
 on conflict(order_id) do update set
  provider_order_id=excluded.provider_order_id,shipment_id=excluded.shipment_id,
  awb_code=excluded.awb_code,courier_name=excluded.courier_name,
  tracking_url=excluded.tracking_url,status=excluded.status,updated_at=now()
 returning id into v_id;
 insert into public.agrinex_notifications(user_id,type,title,message)
 values(v_order.buyer_id,'delivery','Shipment booked','A shipment was booked for order '||v_order.order_number||'.');
 return jsonb_build_object('shipment_id',v_id,'status',p_status);
end;
$$;
revoke all on function public.record_agrinex_shipment(uuid,text,text,text,text,text,text,text,text,text,numeric,numeric,numeric,numeric,text) from public,anon;
grant execute on function public.record_agrinex_shipment(uuid,text,text,text,text,text,text,text,text,text,numeric,numeric,numeric,numeric,text) to authenticated;

create or replace function public.admin_import_agrinex_market_prices(p_rows jsonb)
returns jsonb language plpgsql security definer set search_path='' as $$
declare v_count integer;
begin
 if auth.uid() is null or not exists(select 1 from private.agrinex_admins a where a.user_id=auth.uid()) then
  raise exception 'Admin access required.' using errcode='42501';
 end if;
 if jsonb_typeof(p_rows)<>'array' or jsonb_array_length(p_rows)=0 or jsonb_array_length(p_rows)>500 then
  raise exception 'Provide 1 to 500 verified market price rows.' using errcode='22023';
 end if;
 insert into public.agrinex_market_price_observations(
  state,district,market,commodity,variety,grade,arrival_date,min_price,max_price,
  modal_price,price_unit,source,source_url
 )
 select
  trim(x->>'state'),trim(x->>'district'),trim(x->>'market'),trim(x->>'commodity'),
  nullif(trim(x->>'variety'),''),nullif(trim(x->>'grade'),''),
  (x->>'arrival_date')::date,(x->>'min_price')::numeric,(x->>'max_price')::numeric,
  (x->>'modal_price')::numeric,coalesce(nullif(x->>'price_unit',''),'INR_PER_QUINTAL'),
  coalesce(nullif(x->>'source',''),'AGMARKNET_DATA_GOV_IN'),
  coalesce(nullif(x->>'source_url',''),'https://data.gov.in/catalog/current-daily-price-various-commodities-various-markets-mandi')
 from jsonb_array_elements(p_rows) x
 on conflict(state,district,market,commodity,variety,arrival_date,source)
 do update set min_price=excluded.min_price,max_price=excluded.max_price,
   modal_price=excluded.modal_price,grade=excluded.grade,imported_at=now();
 get diagnostics v_count = row_count;
 return jsonb_build_object('upserted',v_count);
end;
$$;
revoke all on function public.admin_import_agrinex_market_prices(jsonb) from public,anon;
grant execute on function public.admin_import_agrinex_market_prices(jsonb) to authenticated;

create or replace function public.admin_set_agrinex_route_account(p_farmer_id uuid,p_account_id text)
returns jsonb language plpgsql security definer set search_path='' as $$
begin
 if auth.uid() is null or not exists(select 1 from private.agrinex_admins a where a.user_id=auth.uid()) then
  raise exception 'Admin access required.' using errcode='42501';
 end if;
 update public.agrinex_profiles
 set razorpay_route_account_id=nullif(trim(p_account_id),''),updated_at=now()
 where id=p_farmer_id and role='farmer';
 if not found then raise exception 'Farmer profile not found.' using errcode='P0002'; end if;
 return jsonb_build_object('farmer_id',p_farmer_id,'linked',nullif(trim(p_account_id),'') is not null);
end;
$$;
revoke all on function public.admin_set_agrinex_route_account(uuid,text) from public,anon;
grant execute on function public.admin_set_agrinex_route_account(uuid,text) to authenticated;

create or replace function public.admin_set_agrinex_shiprocket_pickup(p_farmer_id uuid,p_pickup_location text)
returns jsonb language plpgsql security definer set search_path='' as $$
begin
 if auth.uid() is null or not exists(select 1 from private.agrinex_admins a where a.user_id=auth.uid()) then
  raise exception 'Admin access required.' using errcode='42501';
 end if;
 update public.agrinex_profiles
 set shiprocket_pickup_location=nullif(trim(p_pickup_location),''),updated_at=now()
 where id=p_farmer_id and role='farmer';
 if not found then raise exception 'Farmer profile not found.' using errcode='P0002'; end if;
 return jsonb_build_object('farmer_id',p_farmer_id,'pickup_location',p_pickup_location);
end;
$$;
revoke all on function public.admin_set_agrinex_shiprocket_pickup(uuid,text) from public,anon;
grant execute on function public.admin_set_agrinex_shiprocket_pickup(uuid,text) to authenticated;

create or replace function public.admin_confirm_agrinex_cod_collection(p_order_id uuid)
returns jsonb language plpgsql security definer set search_path='' as $$
declare v_order public.agrinex_orders%rowtype;
begin
 if auth.uid() is null or not exists(select 1 from private.agrinex_admins a where a.user_id=auth.uid()) then
  raise exception 'Admin access required.' using errcode='42501';
 end if;
 select * into v_order from public.agrinex_orders where id=p_order_id for update;
 if not found then raise exception 'Order not found.' using errcode='P0002'; end if;
 if v_order.payment_method<>'cash_on_delivery' or v_order.status<>'delivered' then
  raise exception 'Only delivered COD orders can be marked as collected.' using errcode='22023';
 end if;
 update public.agrinex_orders set payment_status='collected',updated_at=now() where id=p_order_id;
 insert into public.agrinex_notifications(user_id,type,title,message)
 values(v_order.farmer_id,'payment','COD collection recorded','COD collection was marked as confirmed for order '||v_order.order_number||'.');
 return jsonb_build_object('order_id',p_order_id,'payment_status','collected');
end;
$$;
revoke all on function public.admin_confirm_agrinex_cod_collection(uuid) from public,anon;
grant execute on function public.admin_confirm_agrinex_cod_collection(uuid) to authenticated;
