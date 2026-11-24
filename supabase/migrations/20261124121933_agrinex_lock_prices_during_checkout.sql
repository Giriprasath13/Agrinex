-- Lock all selected product rows before computing checkout totals.
-- This keeps unit prices, stock checks and stock decrements consistent in one transaction.
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

 -- Lock all rows in stable UUID order before reading prices and stock.
 perform p.id
 from public.agrinex_products p
 join jsonb_array_elements(p_items) i on p.id=(i->>'product_id')::uuid
 order by p.id
 for update of p;

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
   order by p.id
  loop
   select (i->>'quantity')::numeric into v_qty
   from jsonb_array_elements(p_items) i
   where (i->>'product_id')::uuid=v_product.id
   limit 1;
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
 insert into public.agrinex_notifications(user_id,type,title,message)
 values(v_buyer,'order','Order placed','Your AgriNex order has been sent to the farmer.');
 return jsonb_build_object('orders',v_all);
end;
$$;
revoke all on function public.create_agrinex_orders(jsonb,jsonb,text) from public,anon;
grant execute on function public.create_agrinex_orders(jsonb,jsonb,text) to authenticated;
