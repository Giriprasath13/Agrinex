-- Make WhatsApp target lookup callable only by the server service role.
-- The service role is the only caller and the function returns only explicitly opted-in contacts.
create or replace function public.get_agrinex_whatsapp_targets(p_order_id uuid)
returns table(user_id uuid,phone text,role text,language text)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_order public.agrinex_orders%rowtype;
begin
  if current_setting('request.jwt.claim.role',true) is distinct from 'service_role' then
    raise exception 'Server access required.' using errcode='42501';
  end if;
  select * into v_order from public.agrinex_orders where id=p_order_id;
  if not found then
    raise exception 'Order not found.' using errcode='P0002';
  end if;
  return query
  select p.id,coalesce(nullif(p.whatsapp_phone,''),p.phone),p.role,p.whatsapp_language
  from public.agrinex_profiles p
  where p.id in (v_order.buyer_id,v_order.farmer_id)
    and p.whatsapp_opt_in=true
    and nullif(coalesce(p.whatsapp_phone,p.phone),'') is not null;
end;
$$;
revoke all on function public.get_agrinex_whatsapp_targets(uuid) from public,anon,authenticated;
grant execute on function public.get_agrinex_whatsapp_targets(uuid) to service_role;
