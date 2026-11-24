CREATE OR REPLACE FUNCTION private.enforce_agrinex_product_owner_fields()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
declare
  v_profile public.agrinex_profiles%rowtype;
  v_internal_update boolean :=
    current_setting('agrinex.internal_product_update', true) = 'on';
begin
  if not v_internal_update
     and (auth.uid() is null or auth.uid() <> new.farmer_id) then
    raise exception 'Product ownership cannot be changed.' using errcode='42501';
  end if;

  select * into v_profile
  from public.agrinex_profiles
  where id = new.farmer_id and role = 'farmer';

  if not found then
    raise exception 'A valid farmer profile is required.' using errcode='42501';
  end if;

  new.farmer_name := v_profile.full_name;
  new.farmer_farm_name := coalesce(v_profile.farm_name, 'Farm');
  new.farmer_verified := (v_profile.farmer_status = 'approved');

  if v_profile.farmer_status <> 'approved' and new.status = 'active' then
    new.status := 'draft';
  end if;

  new.updated_at := now();
  return new;
end;
$function$


revoke all on function private.enforce_agrinex_product_owner_fields() from public, anon, authenticated;

CREATE OR REPLACE FUNCTION private.sync_agrinex_farmer_verification()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
begin
  if old.farmer_status is distinct from new.farmer_status then
    perform set_config('agrinex.internal_product_update', 'on', true);

    update public.agrinex_products
    set farmer_verified = (new.farmer_status = 'approved'),
        status = case
          when new.farmer_status = 'approved' then
            case when status = 'paused' then 'active' else status end
          when status = 'active' then 'paused'
          else status
        end,
        updated_at = now()
    where farmer_id = new.id;

    perform set_config('agrinex.internal_product_update', 'off', true);
  end if;
  return new;
end;
$function$


revoke all on function private.sync_agrinex_farmer_verification() from public, anon, authenticated;

CREATE OR REPLACE FUNCTION public.create_agrinex_orders(p_items jsonb, p_address jsonb, p_payment_method text DEFAULT 'cash_on_delivery'::text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
declare
  v_buyer uuid := auth.uid();
  v_buyer_role text;
  v_farmer_id uuid;
  v_order_id uuid;
  v_order_number text;
  v_product record;
  v_qty numeric(12,3);
  v_subtotal numeric(12,2);
  v_total numeric(12,2);
  v_all jsonb := '[]'::jsonb;
  v_one jsonb;
  v_input_count integer;
  v_found_count integer;
  v_distinct_count integer;
begin
  if v_buyer is null then
    raise exception 'Please sign in before placing an order.' using errcode='42501';
  end if;
  select role into v_buyer_role
  from public.agrinex_profiles where id = v_buyer;
  if v_buyer_role is distinct from 'customer' then
    raise exception 'Only customer accounts can place marketplace orders.' using errcode='42501';
  end if;
  if p_payment_method <> 'cash_on_delivery' then
    raise exception 'Online payment is not enabled yet. Choose Cash on Delivery.' using errcode='22023';
  end if;
  if jsonb_typeof(p_items) <> 'array' or jsonb_array_length(p_items) = 0 then
    raise exception 'Your cart is empty.' using errcode='22023';
  end if;

  v_input_count := jsonb_array_length(p_items);
  select count(*), count(distinct (i->>'product_id')::uuid)
    into v_found_count, v_distinct_count
  from jsonb_array_elements(p_items) i;
  if v_found_count <> v_input_count or v_distinct_count <> v_input_count then
    raise exception 'Cart contains duplicate or invalid product IDs.' using errcode='22023';
  end if;
  if p_address is null
     or nullif(trim(p_address->>'line1'), '') is null
     or nullif(trim(p_address->>'city'), '') is null
     or nullif(trim(p_address->>'state'), '') is null
     or coalesce(p_address->>'pincode', '') !~ '^[0-9]{6}$' then
    raise exception 'Add a complete delivery address before ordering.' using errcode='22023';
  end if;
  if exists (
    select 1 from jsonb_array_elements(p_items) i
    where coalesce((i->>'quantity')::numeric, 0) <= 0
  ) then
    raise exception 'All item quantities must be greater than zero.' using errcode='22023';
  end if;

  select count(*) into v_found_count
  from jsonb_array_elements(p_items) i
  join public.agrinex_products p on p.id = (i->>'product_id')::uuid
  join public.agrinex_profiles f on f.id = p.farmer_id
  where p.status = 'active'
    and p.farmer_verified = true
    and f.role = 'farmer'
    and f.farmer_status = 'approved';
  if v_found_count <> v_input_count then
    raise exception 'One or more products are unavailable or their farmer is not approved. Refresh your cart.' using errcode='P0001';
  end if;

  -- Lock all selected products in stable UUID order before price/stock reads.
  perform p.id
  from public.agrinex_products p
  join jsonb_array_elements(p_items) i on p.id = (i->>'product_id')::uuid
  order by p.id
  for update of p;

  for v_farmer_id in
    select distinct p.farmer_id
    from jsonb_array_elements(p_items) i
    join public.agrinex_products p on p.id = (i->>'product_id')::uuid
    join public.agrinex_profiles f on f.id = p.farmer_id
    where p.status = 'active'
      and p.farmer_verified = true
      and f.farmer_status = 'approved'
  loop
    select round(sum((i->>'quantity')::numeric * p.price), 2)
      into v_subtotal
    from jsonb_array_elements(p_items) i
    join public.agrinex_products p on p.id = (i->>'product_id')::uuid
    where p.farmer_id = v_farmer_id and p.status = 'active';

    v_total := v_subtotal;
    v_order_number := 'AN' || to_char(now(),'YYMMDD')
      || upper(substr(replace(gen_random_uuid()::text,'-',''),1,6));

    insert into public.agrinex_orders(
      order_number,buyer_id,farmer_id,subtotal,delivery_fee,total,
      payment_method,payment_status,delivery_address,status
    ) values (
      v_order_number,v_buyer,v_farmer_id,v_subtotal,0,v_total,
      'cash_on_delivery','pending_collection',p_address,'pending_acceptance'
    ) returning id into v_order_id;

    -- This function has already checked the buyer, products, and locked rows.
    perform set_config('agrinex.internal_product_update', 'on', true);

    for v_product in
      select p.id,p.name,p.unit,p.price,p.market_price,p.stock
      from jsonb_array_elements(p_items) i
      join public.agrinex_products p on p.id = (i->>'product_id')::uuid
      join public.agrinex_profiles f on f.id = p.farmer_id
      where p.farmer_id = v_farmer_id
        and p.status = 'active'
        and p.farmer_verified = true
        and f.farmer_status = 'approved'
      order by p.id
    loop
      select (i->>'quantity')::numeric into v_qty
      from jsonb_array_elements(p_items) i
      where (i->>'product_id')::uuid = v_product.id
      limit 1;

      if v_qty is null or v_qty <= 0 then
        raise exception 'Invalid quantity for %.', v_product.name using errcode='22023';
      end if;
      if v_product.stock < v_qty then
        raise exception 'Not enough stock for %. Available: % %.',
          v_product.name,v_product.stock,v_product.unit using errcode='P0001';
      end if;

      update public.agrinex_products
      set stock = stock - v_qty
      where id = v_product.id and stock >= v_qty;
      if not found then
        raise exception 'Stock changed while placing your order. Please refresh and try again.' using errcode='P0001';
      end if;

      insert into public.agrinex_order_items(
        order_id,product_id,farmer_id,product_name,unit,quantity,
        unit_price,market_price,line_total,platform_fee,delivery_fee,farmer_payout,status
      ) values (
        v_order_id,v_product.id,v_farmer_id,v_product.name,v_product.unit,v_qty,
        v_product.price,v_product.market_price,round(v_product.price*v_qty,2),
        0,0,round(v_product.price*v_qty,2),'pending_acceptance'
      );
    end loop;

    perform set_config('agrinex.internal_product_update', 'off', true);

    insert into public.agrinex_notifications(user_id,type,title,message)
    values (
      v_farmer_id,'order','New order received',
      'A customer placed order ' || v_order_number || '. Please review and accept it.'
    );

    v_one := jsonb_build_object(
      'id',v_order_id,'order_number',v_order_number,
      'status','pending_acceptance','total',v_total
    );
    v_all := v_all || jsonb_build_array(v_one);
  end loop;

  if jsonb_array_length(v_all) = 0 then
    raise exception 'No currently available products could be ordered.' using errcode='P0001';
  end if;

  insert into public.agrinex_notifications(user_id,type,title,message)
  values (v_buyer,'order','Order placed','Your AgriNex order has been sent to the farmer.');
  return jsonb_build_object('orders',v_all);
end;
$function$


revoke all on function public.create_agrinex_orders(jsonb,jsonb,text) from public, anon;

grant execute on function public.create_agrinex_orders(jsonb,jsonb,text) to authenticated;

CREATE OR REPLACE FUNCTION public.update_agrinex_order_status(p_order_id uuid, p_status text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
declare
  v_user uuid := auth.uid();
  v_order public.agrinex_orders%rowtype;
  v_is_farmer boolean;
  v_farmer_status text;
begin
  if v_user is null then
    raise exception 'Please sign in.' using errcode='42501';
  end if;
  select * into v_order from public.agrinex_orders where id = p_order_id for update;
  if not found then
    raise exception 'Order not found.' using errcode='P0002';
  end if;

  v_is_farmer := v_order.farmer_id = v_user;
  if v_order.buyer_id <> v_user and not v_is_farmer then
    raise exception 'You are not allowed to update this order.' using errcode='42501';
  end if;

  if v_is_farmer then
    select farmer_status into v_farmer_status
    from public.agrinex_profiles where id=v_user and role='farmer';
    if v_farmer_status is distinct from 'approved' then
      raise exception 'Only approved farmers can update marketplace orders.' using errcode='42501';
    end if;
  end if;

  if v_order.buyer_id = v_user then
    if p_status <> 'cancelled'
       or v_order.status not in ('pending_acceptance','confirmed') then
      raise exception 'Customers can cancel only orders that have not been prepared.' using errcode='42501';
    end if;
  else
    if p_status not in ('confirmed','preparing','ready_for_pickup','picked_up','out_for_delivery','delivered','cancelled') then
      raise exception 'Invalid farmer order status.' using errcode='22023';
    end if;
    if p_status='cancelled' and v_order.status not in ('pending_acceptance','confirmed') then
      raise exception 'This order can no longer be cancelled by the farmer.' using errcode='42501';
    end if;
    if p_status='confirmed' and v_order.status<>'pending_acceptance' then
      raise exception 'Only new orders can be accepted.' using errcode='42501';
    end if;
    if p_status='preparing' and v_order.status<>'confirmed' then
      raise exception 'Accept the order before preparing it.' using errcode='42501';
    end if;
    if p_status='ready_for_pickup' and v_order.status<>'preparing' then
      raise exception 'Mark the order as preparing before it is ready.' using errcode='42501';
    end if;
    if p_status='picked_up' and v_order.status<>'ready_for_pickup' then
      raise exception 'The order must be ready before pickup.' using errcode='42501';
    end if;
    if p_status='out_for_delivery' and v_order.status not in ('ready_for_pickup','picked_up') then
      raise exception 'The order must be ready for delivery first.' using errcode='42501';
    end if;
    if p_status='delivered' and v_order.status not in ('picked_up','out_for_delivery') then
      raise exception 'The order must be picked up before delivery confirmation.' using errcode='42501';
    end if;
  end if;

  if p_status='cancelled' then
    perform set_config('agrinex.internal_product_update', 'on', true);

    update public.agrinex_products p
      set stock = p.stock + i.quantity
    from public.agrinex_order_items i
    where i.order_id = p_order_id and i.product_id = p.id;

    perform set_config('agrinex.internal_product_update', 'off', true);

    update public.agrinex_order_items
    set status='cancelled',updated_at=now()
    where order_id=p_order_id;
  else
    update public.agrinex_order_items
    set status=p_status,updated_at=now()
    where order_id=p_order_id;
  end if;

  update public.agrinex_orders set status=p_status,updated_at=now() where id=p_order_id;

  insert into public.agrinex_notifications(user_id,type,title,message)
  values (
    case when v_is_farmer then v_order.buyer_id else v_order.farmer_id end,
    'order','Order status updated',
    'Order '||v_order.order_number||' is now '||replace(p_status,'_',' ')||'.'
  );

  return jsonb_build_object('id',p_order_id,'order_number',v_order.order_number,'status',p_status);
end;
$function$


revoke all on function public.update_agrinex_order_status(uuid,text) from public, anon;

grant execute on function public.update_agrinex_order_status(uuid,text) to authenticated;

drop policy if exists "Offer parties can update permitted offer states" on public.agrinex_bulk_offers;
create policy "Offer parties can update permitted offer states"
on public.agrinex_bulk_offers for update to authenticated
using (
  (farmer_id=(select auth.uid()) and status='pending'
   and exists(select 1 from public.agrinex_profiles p where p.id=(select auth.uid()) and p.role='farmer' and p.farmer_status='approved'))
  or exists(select 1 from public.agrinex_bulk_requests r where r.id=request_id and r.buyer_id=(select auth.uid()) and r.status in ('open','offers_received'))
)
with check (
  (farmer_id=(select auth.uid()) and status='withdrawn')
  or (status in ('accepted','rejected') and exists(
    select 1 from public.agrinex_bulk_requests r where r.id=request_id and r.buyer_id=(select auth.uid()) and r.status in ('open','offers_received')
  ))
);