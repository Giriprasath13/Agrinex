
-- Courier contact email, server-only WhatsApp target lookup and payment-failure events.
alter table public.agrinex_addresses
  add column if not exists email text;

revoke all on function public.get_agrinex_whatsapp_targets(uuid)
  from public,anon,authenticated;
grant execute on function public.get_agrinex_whatsapp_targets(uuid)
  to service_role;

create or replace function public.record_agrinex_razorpay_failure(
  p_order_id uuid,
  p_provider_order_id text,
  p_payment_id text,
  p_event_id text,
  p_failure_reason text default null
) returns jsonb
language plpgsql security definer set search_path='' as $$
declare
  v_order public.agrinex_orders%rowtype;
  v_inserted_count integer;
begin
  if current_setting('request.jwt.claim.role',true) <> 'service_role' then
    raise exception 'Server verification required.' using errcode='42501';
  end if;
  if nullif(trim(p_event_id),'') is null then
    raise exception 'Payment event reference is required.' using errcode='22023';
  end if;
  insert into private.agrinex_provider_events(provider,provider_event_id,event_type)
  values('razorpay',p_event_id,'payment.failed')
  on conflict(provider,provider_event_id) do nothing;
  get diagnostics v_inserted_count = row_count;
  if v_inserted_count=0 then
    return jsonb_build_object('duplicate',true,'payment_status','unchanged');
  end if;
  select * into v_order from public.agrinex_orders
  where id=p_order_id for update;
  if not found or v_order.payment_method<>'razorpay'
     or v_order.provider_order_id<>p_provider_order_id then
    raise exception 'Payment failure does not match the saved AgriNex order.' using errcode='42501';
  end if;
  if v_order.payment_status='captured' then
    return jsonb_build_object('order_id',p_order_id,'payment_status','captured','ignored_late_failure',true);
  end if;
  if v_order.payment_status in ('awaiting_payment','authorized') then
    update public.agrinex_orders
    set payment_status='failed',updated_at=now()
    where id=p_order_id;
    insert into public.agrinex_notifications(user_id,type,title,message)
    values(v_order.buyer_id,'payment','Payment attempt failed',
      'Payment attempt for '||v_order.order_number||' failed. The order is not paid; a later successful payment event can still confirm it.');
  end if;
  return jsonb_build_object(
    'order_id',p_order_id,
    'payment_status',(select payment_status from public.agrinex_orders where id=p_order_id),
    'stock_reserved',true
  );
end;
$$;
revoke all on function public.record_agrinex_razorpay_failure(uuid,text,text,text,text)
  from public,anon,authenticated;
grant execute on function public.record_agrinex_razorpay_failure(uuid,text,text,text,text)
  to service_role;
