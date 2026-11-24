-- Keep approved farmer drafts unpublished until the farmer explicitly publishes them.
create or replace function private.enforce_agrinex_product_owner_fields()
returns trigger language plpgsql security definer set search_path='' as $$
declare
 v_profile public.agrinex_profiles%rowtype;
 v_internal_update boolean:=current_setting('agrinex.internal_product_update',true)='on';
begin
 if not v_internal_update and (auth.uid() is null or auth.uid()<>new.farmer_id) then
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
