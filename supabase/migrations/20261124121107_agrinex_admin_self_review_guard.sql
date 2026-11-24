-- Disallow administrators from reviewing their own farmer profile.
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
