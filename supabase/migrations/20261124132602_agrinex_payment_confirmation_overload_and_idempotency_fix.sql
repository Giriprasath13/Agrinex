-- Server-only, idempotent Razorpay capture confirmation.
create or replace function public.confirm_agrinex_razorpay_payment(
  p_order_id uuid,p_provider_order_id text,p_payment_id text,p_event_id text
) returns jsonb
language plpgsql security definer set search_path='' as $$
declare
  v_order public.agrinex_orders%rowtype;
  v_inserted_count integer;
begin
  if current_setting('request.jwt.claim.role',true) <> 'service_role' then
    raise exception 'Server verification required.' using errcode='42501';
  end if;
  if nullif(trim(p_event_id),'') is null or nullif(trim(p_payment_id),'') is null then
    raise exception 'Payment event reference is required.' using errcode='22023';
  end if;
  insert into private.agrinex_provider_events(provider,provider_event_id,event_type)
  values('razorpay',p_event_id,'payment.captured')
  on conflict(provider,provider_event_id) do nothing;
  get diagnostics v_inserted_count = row_count;
  if v_inserted_count=0 then
    return jsonb_build_object('duplicate',true,'payment_status','unchanged');
  end if;
  select * into v_order from public.agrinex_orders where id=p_order_id for update;
  if not found or v_order.payment_method<>'razorpay'
     or v_order.provider_order_id<>p_provider_order_id then
    raise exception 'Payment does not match the saved AgriNex order.' using errcode='42501';
  end if;
  if v_order.payment_status='captured' and v_order.provider_payment_id=p_payment_id then
    return jsonb_build_object('order_id',p_order_id,'payment_status','captured','already_confirmed',true);
  end if;
  if v_order.payment_status not in ('awaiting_payment','authorized','failed') then
    raise exception 'This order is not awaiting payment confirmation.' using errcode='22023';
  end if;
  update public.agrinex_orders
  set payment_status='captured',provider_payment_id=p_payment_id,
      payment_verified_at=now(),payment_provider='razorpay',updated_at=now()
  where id=p_order_id;
  insert into public.agrinex_notifications(user_id,type,title,message)
  values(v_order.buyer_id,'payment','Payment confirmed','Payment for order '||v_order.order_number||' was verified.');
  insert into public.agrinex_notifications(user_id,type,title,message)
  values(v_order.farmer_id,'order','New prepaid order','Payment for order '||v_order.order_number||' was verified. Please review the order.');
  return jsonb_build_object('order_id',p_order_id,'payment_status','captured');
end;
$$;
revoke all on function public.confirm_agrinex_razorpay_payment(uuid,text,text,text)
  from public,anon,authenticated;
grant execute on function public.confirm_agrinex_razorpay_payment(uuid,text,text,text)
  to service_role;
