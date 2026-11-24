-- AgriNex shared farmer-first marketplace schema.
-- Apply after the AgriNex auth profile migration. This migration is source control
-- for the schema already installed in the dedicated AgriNex Supabase project.

alter table public.agrinex_profiles
  add column if not exists farm_village text,
  add column if not exists farm_district text,
  add column if not exists farm_pincode text,
  add column if not exists farm_address text,
  add column if not exists preferred_language text not null default 'en'
    check (preferred_language in ('en','ta')),
  add column if not exists farm_type text,
  add column if not exists farm_description text,
  add column if not exists verification_submitted_at timestamptz,
  add column if not exists verification_note text,
  add column if not exists updated_at timestamptz not null default now();

grant update (full_name, farm_name, farm_village, farm_district, farm_pincode,
              farm_address, preferred_language, farm_type, farm_description,
              verification_submitted_at, updated_at)
on public.agrinex_profiles to authenticated;

drop policy if exists "Users can update their own AgriNex profile" on public.agrinex_profiles;
create policy "Users can update their own AgriNex profile"
on public.agrinex_profiles for update to authenticated
using ((select auth.uid()) = id)
with check ((select auth.uid()) = id);

create table if not exists public.agrinex_products (
  id uuid primary key default gen_random_uuid(),
  farmer_id uuid not null references public.agrinex_profiles(id) on delete restrict,
  farmer_name text not null,
  farmer_farm_name text not null,
  name text not null check (length(trim(name)) between 2 and 100),
  description text not null default '',
  category text not null check (category in ('Vegetables','Fruits','Grains','Dairy','Liquids','Organic','Other')),
  variety text,
  grade text,
  price numeric(12,2) not null check (price > 0),
  market_price numeric(12,2) not null check (market_price > 0),
  stock numeric(12,3) not null default 0 check (stock >= 0),
  unit text not null default 'kg' check (unit in ('kg','g','unit','L','ml','pack','bag','crate','dozen')),
  harvest_date date,
  available_until date,
  organic boolean not null default false,
  image_url text,
  status text not null default 'draft' check (status in ('draft','active','paused','archived','rejected')),
  village text,
  district text,
  pincode text,
  delivery_minutes integer not null default 0 check (delivery_minutes >= 0),
  pickup_available boolean not null default true,
  delivery_available boolean not null default false,
  delivery_notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint agrinex_product_market_reference check (market_price >= price)
);
create index if not exists agrinex_products_public_idx on public.agrinex_products(status,category,created_at desc);
create index if not exists agrinex_products_farmer_idx on public.agrinex_products(farmer_id,status,created_at desc);
create index if not exists agrinex_products_location_idx on public.agrinex_products(pincode,district) where status='active';
alter table public.agrinex_products enable row level security;
revoke all on public.agrinex_products from anon,authenticated;
grant select on public.agrinex_products to anon,authenticated;
grant insert,update,delete on public.agrinex_products to authenticated;
drop policy if exists "Public and farmers can read AgriNex products" on public.agrinex_products;
create policy "Public and farmers can read AgriNex products"
on public.agrinex_products for select to anon,authenticated
using (status='active' or farmer_id=(select auth.uid()));
drop policy if exists "Farmers can create AgriNex product drafts" on public.agrinex_products;
create policy "Farmers can create AgriNex product drafts"
on public.agrinex_products for insert to authenticated
with check (
 farmer_id=(select auth.uid())
 and exists(select 1 from public.agrinex_profiles p where p.id=(select auth.uid()) and p.role='farmer'
            and (status='draft' or p.farmer_status='approved'))
);
drop policy if exists "Farmers can update their AgriNex products" on public.agrinex_products;
create policy "Farmers can update their AgriNex products"
on public.agrinex_products for update to authenticated
using (farmer_id=(select auth.uid()) and exists(select 1 from public.agrinex_profiles p where p.id=(select auth.uid()) and p.role='farmer'))
with check (
 farmer_id=(select auth.uid())
 and exists(select 1 from public.agrinex_profiles p where p.id=(select auth.uid()) and p.role='farmer'
            and (status in ('draft','paused','archived') or p.farmer_status='approved'))
);
drop policy if exists "Farmers can delete their AgriNex products" on public.agrinex_products;
create policy "Farmers can delete their AgriNex products"
on public.agrinex_products for delete to authenticated
using (farmer_id=(select auth.uid()));

create table if not exists public.agrinex_addresses (
 id uuid primary key default gen_random_uuid(),
 user_id uuid not null references public.agrinex_profiles(id) on delete cascade,
 label text not null default 'Home',
 recipient_name text,
 mobile text,
 line1 text not null,
 city text not null,
 state text not null,
 pincode text not null check (pincode ~ '^[0-9]{6}$'),
 landmark text,
 created_at timestamptz not null default now()
);
create index if not exists agrinex_addresses_user_idx on public.agrinex_addresses(user_id,created_at);
alter table public.agrinex_addresses enable row level security;
revoke all on public.agrinex_addresses from anon,authenticated;
grant select,insert,update,delete on public.agrinex_addresses to authenticated;
drop policy if exists "Users manage their own AgriNex addresses" on public.agrinex_addresses;
create policy "Users manage their own AgriNex addresses"
on public.agrinex_addresses for all to authenticated
using (user_id=(select auth.uid()))
with check (user_id=(select auth.uid()));

create table if not exists public.agrinex_cart_items (
 id uuid primary key default gen_random_uuid(),
 user_id uuid not null references public.agrinex_profiles(id) on delete cascade,
 product_id uuid not null references public.agrinex_products(id) on delete cascade,
 quantity numeric(12,3) not null check (quantity>0 and quantity<=100000),
 created_at timestamptz not null default now(),
 updated_at timestamptz not null default now(),
 unique(user_id,product_id)
);
create index if not exists agrinex_cart_items_product_idx on public.agrinex_cart_items(product_id);
alter table public.agrinex_cart_items enable row level security;
revoke all on public.agrinex_cart_items from anon,authenticated;
grant select,insert,update,delete on public.agrinex_cart_items to authenticated;
drop policy if exists "Users manage their own cart" on public.agrinex_cart_items;
create policy "Users manage their own cart"
on public.agrinex_cart_items for all to authenticated
using (user_id=(select auth.uid()))
with check (user_id=(select auth.uid()));

create table if not exists public.agrinex_orders (
 id uuid primary key default gen_random_uuid(),
 order_number text not null unique,
 buyer_id uuid not null references public.agrinex_profiles(id) on delete restrict,
 farmer_id uuid not null references public.agrinex_profiles(id) on delete restrict,
 status text not null default 'pending_acceptance'
   check (status in ('pending_acceptance','confirmed','preparing','ready_for_pickup','picked_up','out_for_delivery','delivered','cancelled')),
 subtotal numeric(12,2) not null check (subtotal>=0),
 delivery_fee numeric(12,2) not null default 0 check (delivery_fee>=0),
 total numeric(12,2) not null check (total>=0),
 payment_method text not null check (payment_method in ('cash_on_delivery')),
 payment_status text not null default 'pending_collection'
   check (payment_status in ('pending_collection','collected','refunded','not_applicable')),
 delivery_address jsonb not null,
 delivery_notes text,
 created_at timestamptz not null default now(),
 updated_at timestamptz not null default now()
);
create index if not exists agrinex_orders_buyer_idx on public.agrinex_orders(buyer_id,created_at desc);
create index if not exists agrinex_orders_farmer_idx on public.agrinex_orders(farmer_id,created_at desc);
create index if not exists agrinex_orders_status_idx on public.agrinex_orders(status,created_at desc);
alter table public.agrinex_orders enable row level security;
revoke all on public.agrinex_orders from anon,authenticated;
grant select on public.agrinex_orders to authenticated;
drop policy if exists "Buyers and assigned farmers can read their orders" on public.agrinex_orders;
create policy "Buyers and assigned farmers can read their orders"
on public.agrinex_orders for select to authenticated
using (buyer_id=(select auth.uid()) or farmer_id=(select auth.uid()));

create table if not exists public.agrinex_order_items (
 id uuid primary key default gen_random_uuid(),
 order_id uuid not null references public.agrinex_orders(id) on delete cascade,
 product_id uuid references public.agrinex_products(id) on delete set null,
 farmer_id uuid not null references public.agrinex_profiles(id) on delete restrict,
 product_name text not null,
 unit text not null,
 quantity numeric(12,3) not null check (quantity>0),
 unit_price numeric(12,2) not null check (unit_price>0),
 market_price numeric(12,2) not null check (market_price>0),
 line_total numeric(12,2) not null check (line_total>=0),
 platform_fee numeric(12,2) not null default 0 check (platform_fee>=0),
 delivery_fee numeric(12,2) not null default 0 check (delivery_fee>=0),
 farmer_payout numeric(12,2) not null check (farmer_payout>=0),
 status text not null default 'pending_acceptance'
   check (status in ('pending_acceptance','confirmed','preparing','ready_for_pickup','picked_up','out_for_delivery','delivered','cancelled')),
 payout_status text not null default 'not_settled'
   check (payout_status in ('not_settled','settlement_pending','settled')),
 created_at timestamptz not null default now(),
 updated_at timestamptz not null default now()
);
create index if not exists agrinex_order_items_order_idx on public.agrinex_order_items(order_id);
create index if not exists agrinex_order_items_farmer_idx on public.agrinex_order_items(farmer_id,status,created_at desc);
create index if not exists agrinex_order_items_product_idx on public.agrinex_order_items(product_id);
alter table public.agrinex_order_items enable row level security;
revoke all on public.agrinex_order_items from anon,authenticated;
grant select on public.agrinex_order_items to authenticated;
drop policy if exists "Buyers and assigned farmers can read order items" on public.agrinex_order_items;
create policy "Buyers and assigned farmers can read order items"
on public.agrinex_order_items for select to authenticated
using (
 farmer_id=(select auth.uid())
 or exists(select 1 from public.agrinex_orders o where o.id=order_id and o.buyer_id=(select auth.uid()))
);

create table if not exists public.agrinex_notifications (
 id uuid primary key default gen_random_uuid(),
 user_id uuid not null references public.agrinex_profiles(id) on delete cascade,
 type text not null default 'general',
 title text not null,
 message text not null,
 read boolean not null default false,
 created_at timestamptz not null default now()
);
create index if not exists agrinex_notifications_user_idx on public.agrinex_notifications(user_id,created_at desc);
alter table public.agrinex_notifications enable row level security;
revoke all on public.agrinex_notifications from anon,authenticated;
grant select,update(read) on public.agrinex_notifications to authenticated;
drop policy if exists "Users can read their notifications" on public.agrinex_notifications;
create policy "Users can read their notifications"
on public.agrinex_notifications for select to authenticated
using (user_id=(select auth.uid()));
drop policy if exists "Users can mark their notifications read" on public.agrinex_notifications;
create policy "Users can mark their notifications read"
on public.agrinex_notifications for update to authenticated
using (user_id=(select auth.uid()))
with check (user_id=(select auth.uid()));

create table if not exists public.agrinex_bulk_requests (
 id uuid primary key default gen_random_uuid(),
 buyer_id uuid not null references public.agrinex_profiles(id) on delete cascade,
 buyer_name text not null,
 product_name text not null,
 category text not null,
 quantity numeric(12,3) not null check (quantity>0),
 unit text not null default 'kg',
 target_price numeric(12,2) check (target_price is null or target_price>0),
 needed_by date,
 delivery_pincode text,
 delivery_city text,
 notes text,
 status text not null default 'open'
   check (status in ('open','offers_received','accepted','closed','cancelled')),
 created_at timestamptz not null default now(),
 updated_at timestamptz not null default now()
);
create index if not exists agrinex_bulk_requests_open_idx on public.agrinex_bulk_requests(status,created_at desc);
create index if not exists agrinex_bulk_requests_buyer_idx on public.agrinex_bulk_requests(buyer_id,created_at desc);
alter table public.agrinex_bulk_requests enable row level security;
revoke all on public.agrinex_bulk_requests from anon,authenticated;
grant select,insert,update on public.agrinex_bulk_requests to authenticated;
drop policy if exists "Authenticated buyers and farmers can see open bulk requests" on public.agrinex_bulk_requests;
create policy "Authenticated buyers and farmers can see open bulk requests"
on public.agrinex_bulk_requests for select to authenticated
using (
 buyer_id=(select auth.uid())
 or (status in ('open','offers_received') and exists(
   select 1 from public.agrinex_profiles p where p.id=(select auth.uid()) and p.role='farmer' and p.farmer_status='approved'
 ))
);
drop policy if exists "Customers can create bulk requests" on public.agrinex_bulk_requests;
create policy "Customers can create bulk requests"
on public.agrinex_bulk_requests for insert to authenticated
with check (
 buyer_id=(select auth.uid())
 and exists(select 1 from public.agrinex_profiles p where p.id=(select auth.uid()) and p.role='customer')
);
drop policy if exists "Buyers can update their bulk requests" on public.agrinex_bulk_requests;
create policy "Buyers can update their bulk requests"
on public.agrinex_bulk_requests for update to authenticated
using (buyer_id=(select auth.uid()))
with check (buyer_id=(select auth.uid()));

create table if not exists public.agrinex_bulk_offers (
 id uuid primary key default gen_random_uuid(),
 request_id uuid not null references public.agrinex_bulk_requests(id) on delete cascade,
 farmer_id uuid not null references public.agrinex_profiles(id) on delete restrict,
 product_id uuid references public.agrinex_products(id) on delete set null,
 farmer_name text not null,
 offered_quantity numeric(12,3) not null check (offered_quantity>0),
 unit_price numeric(12,2) not null check (unit_price>0),
 notes text,
 status text not null default 'pending' check (status in ('pending','accepted','rejected','withdrawn')),
 created_at timestamptz not null default now(),
 unique(request_id,farmer_id)
);
create index if not exists agrinex_bulk_offers_farmer_idx on public.agrinex_bulk_offers(farmer_id,created_at desc);
create index if not exists agrinex_bulk_offers_product_idx on public.agrinex_bulk_offers(product_id);
alter table public.agrinex_bulk_offers enable row level security;
revoke all on public.agrinex_bulk_offers from anon,authenticated;
grant select,insert on public.agrinex_bulk_offers to authenticated;
grant update(status) on public.agrinex_bulk_offers to authenticated;
drop policy if exists "Bulk offer parties can read offers" on public.agrinex_bulk_offers;
create policy "Bulk offer parties can read offers"
on public.agrinex_bulk_offers for select to authenticated
using (
 farmer_id=(select auth.uid())
 or exists(select 1 from public.agrinex_bulk_requests r where r.id=request_id and r.buyer_id=(select auth.uid()))
);
drop policy if exists "Approved farmers can make bulk offers" on public.agrinex_bulk_offers;
create policy "Approved farmers can make bulk offers"
on public.agrinex_bulk_offers for insert to authenticated
with check (
 farmer_id=(select auth.uid())
 and exists(select 1 from public.agrinex_profiles p where p.id=(select auth.uid()) and p.role='farmer' and p.farmer_status='approved')
);
drop policy if exists "Farmers can withdraw their own pending offers" on public.agrinex_bulk_offers;
drop policy if exists "Buyers can decide offers on their requests" on public.agrinex_bulk_offers;
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

create schema if not exists private;
revoke all on schema private from public,anon,authenticated;
create table if not exists private.agrinex_admins (
 user_id uuid primary key references auth.users(id) on delete cascade,
 created_at timestamptz not null default now()
);
revoke all on private.agrinex_admins from public,anon,authenticated;

create or replace function public.create_agrinex_orders(p_items jsonb,p_address jsonb,p_payment_method text default 'cash_on_delivery')
returns jsonb language plpgsql security definer set search_path='' as $$
declare
 v_buyer uuid:=auth.uid();
 v_buyer_role text;
 v_farmer_id uuid;
 v_order_id uuid;
 v_order_number text;
 v_product record;
 v_qty numeric(12,3);
 v_subtotal numeric(12,2);
 v_total numeric(12,2);
 v_all jsonb:='[]'::jsonb;
 v_one jsonb;
 v_input_count integer;
 v_found_count integer;
 v_distinct_count integer;
begin
 if v_buyer is null then raise exception 'Please sign in before placing an order.' using errcode='42501'; end if;
 select role into v_buyer_role from public.agrinex_profiles where id=v_buyer;
 if v_buyer_role is distinct from 'customer' then raise exception 'Only customer accounts can place marketplace orders.' using errcode='42501'; end if;
 if p_payment_method<>'cash_on_delivery' then raise exception 'Online payment is not enabled yet. Choose Cash on Delivery.' using errcode='22023'; end if;
 if jsonb_typeof(p_items)<>'array' or jsonb_array_length(p_items)=0 then raise exception 'Your cart is empty.' using errcode='22023'; end if;
 v_input_count:=jsonb_array_length(p_items);
 select count(*),count(distinct (i->>'product_id')::uuid) into v_found_count,v_distinct_count from jsonb_array_elements(p_items) i;
 if v_found_count<>v_input_count or v_distinct_count<>v_input_count then raise exception 'Cart contains duplicate or invalid product IDs.' using errcode='22023'; end if;
 if p_address is null or nullif(trim(p_address->>'line1'),'') is null or nullif(trim(p_address->>'city'),'') is null or nullif(trim(p_address->>'state'),'') is null or coalesce(p_address->>'pincode','') !~ '^[0-9]{6}$' then
  raise exception 'Add a complete delivery address before ordering.' using errcode='22023';
 end if;
 if exists(select 1 from jsonb_array_elements(p_items) i where coalesce((i->>'quantity')::numeric,0)<=0) then raise exception 'All item quantities must be greater than zero.' using errcode='22023'; end if;
 select count(*) into v_found_count
 from jsonb_array_elements(p_items) i
 join public.agrinex_products p on p.id=(i->>'product_id')::uuid
 join public.agrinex_profiles f on f.id=p.farmer_id
 where p.status='active' and p.farmer_verified=true and f.role='farmer' and f.farmer_status='approved';
 if v_found_count<>v_input_count then raise exception 'One or more products are unavailable or their farmer is not approved. Refresh your cart.' using errcode='P0001'; end if;
 for v_farmer_id in
  select distinct p.farmer_id
  from jsonb_array_elements(p_items) i
  join public.agrinex_products p on p.id=(i->>'product_id')::uuid
  join public.agrinex_profiles f on f.id=p.farmer_id
  where p.status='active' and p.farmer_verified=true and f.farmer_status='approved'
 loop
  select round(sum((i->>'quantity')::numeric*p.price),2) into v_subtotal
  from jsonb_array_elements(p_items) i
  join public.agrinex_products p on p.id=(i->>'product_id')::uuid
  where p.farmer_id=v_farmer_id and p.status='active';
  v_total:=v_subtotal;
  v_order_number:='AN'||to_char(now(),'YYMMDD')||upper(substr(replace(gen_random_uuid()::text,'-',''),1,6));
  insert into public.agrinex_orders(order_number,buyer_id,farmer_id,subtotal,delivery_fee,total,payment_method,payment_status,delivery_address,status)
  values(v_order_number,v_buyer,v_farmer_id,v_subtotal,0,v_total,'cash_on_delivery','pending_collection',p_address,'pending_acceptance')
  returning id into v_order_id;
  for v_product in
   select p.id,p.name,p.unit,p.price,p.market_price,p.stock
   from jsonb_array_elements(p_items) i
   join public.agrinex_products p on p.id=(i->>'product_id')::uuid
   join public.agrinex_profiles f on f.id=p.farmer_id
   where p.farmer_id=v_farmer_id and p.status='active' and p.farmer_verified=true and f.farmer_status='approved'
   order by p.id for update of p
  loop
   select (i->>'quantity')::numeric into v_qty from jsonb_array_elements(p_items) i where (i->>'product_id')::uuid=v_product.id limit 1;
   if v_qty is null or v_qty<=0 then raise exception 'Invalid quantity for %.',v_product.name using errcode='22023'; end if;
   if v_product.stock<v_qty then raise exception 'Not enough stock for %. Available: % %.',v_product.name,v_product.stock,v_product.unit using errcode='P0001'; end if;
   update public.agrinex_products set stock=stock-v_qty where id=v_product.id and stock>=v_qty;
   if not found then raise exception 'Stock changed while placing your order. Please refresh and try again.' using errcode='P0001'; end if;
   insert into public.agrinex_order_items(order_id,product_id,farmer_id,product_name,unit,quantity,unit_price,market_price,line_total,platform_fee,delivery_fee,farmer_payout,status)
   values(v_order_id,v_product.id,v_farmer_id,v_product.name,v_product.unit,v_qty,v_product.price,v_product.market_price,round(v_product.price*v_qty,2),0,0,round(v_product.price*v_qty,2),'pending_acceptance');
  end loop;
  insert into public.agrinex_notifications(user_id,type,title,message)
  values(v_farmer_id,'order','New order received','A customer placed order '||v_order_number||'. Please review and accept it.');
  v_one:=jsonb_build_object('id',v_order_id,'order_number',v_order_number,'status','pending_acceptance','total',v_total);
  v_all:=v_all||jsonb_build_array(v_one);
 end loop;
 if jsonb_array_length(v_all)=0 then raise exception 'No currently available products could be ordered.' using errcode='P0001'; end if;
 insert into public.agrinex_notifications(user_id,type,title,message) values(v_buyer,'order','Order placed','Your AgriNex order has been sent to the farmer.');
 return jsonb_build_object('orders',v_all);
end;
$$;
revoke all on function public.create_agrinex_orders(jsonb,jsonb,text) from public,anon;
grant execute on function public.create_agrinex_orders(jsonb,jsonb,text) to authenticated;

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
  update public.agrinex_order_items set status='cancelled' where order_id=p_order_id;
 else
  update public.agrinex_order_items set status=p_status where order_id=p_order_id;
 end if;
 update public.agrinex_orders set status=p_status where id=p_order_id;
 insert into public.agrinex_notifications(user_id,type,title,message)
 values(case when v_is_farmer then v_order.buyer_id else v_order.farmer_id end,'order','Order status updated','Order '||v_order.order_number||' is now '||replace(p_status,'_',' ')||'.');
 return jsonb_build_object('id',p_order_id,'order_number',v_order.order_number,'status',p_status);
end;
$$;
revoke all on function public.update_agrinex_order_status(uuid,text) from public,anon;
grant execute on function public.update_agrinex_order_status(uuid,text) to authenticated;

create or replace function public.admin_list_agrinex_farmer_applications()
returns table(id uuid,phone text,full_name text,farm_name text,farm_village text,farm_district text,farm_pincode text,verification_submitted_at timestamptz,farmer_status text,created_at timestamptz)
language plpgsql security definer set search_path='' as $$
begin
 if auth.uid() is null or not exists(select 1 from private.agrinex_admins a where a.user_id=auth.uid()) then raise exception 'Admin access required.' using errcode='42501'; end if;
 return query select p.id,p.phone,p.full_name,p.farm_name,p.farm_village,p.farm_district,p.farm_pincode,p.verification_submitted_at,p.farmer_status,p.created_at
 from public.agrinex_profiles p where p.role='farmer' and p.id<>auth.uid() and p.farmer_status in ('pending','rejected')
 order by p.verification_submitted_at nulls last,p.created_at;
end;
$$;
revoke all on function public.admin_list_agrinex_farmer_applications() from public,anon;
grant execute on function public.admin_list_agrinex_farmer_applications() to authenticated;

create or replace function public.admin_review_agrinex_farmer_application(p_farmer_id uuid,p_decision text,p_notes text default null)
returns jsonb language plpgsql security definer set search_path='' as $$
declare v_farmer public.agrinex_profiles%rowtype;
begin
 if auth.uid() is null or not exists(select 1 from private.agrinex_admins a where a.user_id=auth.uid()) then raise exception 'Admin access required.' using errcode='42501'; end if;
 if p_farmer_id=auth.uid() then raise exception 'An administrator cannot review their own farmer application.' using errcode='42501'; end if;
 if p_decision not in ('approved','rejected') then raise exception 'Decision must be approved or rejected.' using errcode='22023'; end if;
 update public.agrinex_profiles set farmer_status=p_decision,verification_note=left(coalesce(p_notes,''),500)
 where id=p_farmer_id and role='farmer' returning * into v_farmer;
 if not found then raise exception 'Farmer application not found.' using errcode='P0002'; end if;
 insert into public.agrinex_notifications(user_id,type,title,message)
 values(p_farmer_id,'verification',case when p_decision='approved' then 'Farmer account approved' else 'Farmer application needs attention' end,
 case when p_decision='approved' then 'Your farmer account is approved. You can now publish products.'
 else 'Your farmer application was not approved yet. Review your details and contact AgriNex support.' end);
 return jsonb_build_object('farmer_id',p_farmer_id,'farmer_status',p_decision);
end;
$$;
revoke all on function public.admin_review_agrinex_farmer_application(uuid,text,text) from public,anon;
grant execute on function public.admin_review_agrinex_farmer_application(uuid,text,text) to authenticated;

insert into storage.buckets(id,name,public,file_size_limit,allowed_mime_types)
values('agrinex-product-images','agrinex-product-images',true,5242880,array['image/jpeg','image/png','image/webp'])
on conflict(id) do update set public=true,file_size_limit=5242880,allowed_mime_types=array['image/jpeg','image/png','image/webp'];
drop policy if exists "Public can view AgriNex product images" on storage.objects;
create policy "Public can view AgriNex product images" on storage.objects for select to anon,authenticated using(bucket_id='agrinex-product-images');
drop policy if exists "Farmers upload their own AgriNex product images" on storage.objects;
create policy "Farmers upload their own AgriNex product images" on storage.objects for insert to authenticated
with check(bucket_id='agrinex-product-images' and (storage.foldername(name))[1]=(select auth.uid())::text
 and exists(select 1 from public.agrinex_profiles p where p.id=(select auth.uid()) and p.role='farmer'));
drop policy if exists "Farmers update their own AgriNex product images" on storage.objects;
create policy "Farmers update their own AgriNex product images" on storage.objects for update to authenticated
using(bucket_id='agrinex-product-images' and (storage.foldername(name))[1]=(select auth.uid())::text)
with check(bucket_id='agrinex-product-images' and (storage.foldername(name))[1]=(select auth.uid())::text);
drop policy if exists "Farmers delete their own AgriNex product images" on storage.objects;
create policy "Farmers delete their own AgriNex product images" on storage.objects for delete to authenticated
using(bucket_id='agrinex-product-images' and (storage.foldername(name))[1]=(select auth.uid())::text);
