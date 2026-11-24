-- Private auth.users trigger: create a least-privilege profile on signup.
create schema if not exists private;
revoke all on schema private from public, anon, authenticated;

create or replace function private.handle_agrinex_user_created()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
    requested_role text;
    profile_name text;
    profile_farm text;
begin
    requested_role := case
        when new.raw_user_meta_data ->> 'role' = 'farmer' then 'farmer'
        else 'customer'
    end;
    profile_name := coalesce(
        nullif(trim(new.raw_user_meta_data ->> 'full_name'), ''),
        'AgriNex user'
    );
    profile_farm := nullif(trim(new.raw_user_meta_data ->> 'farm_name'), '');

    insert into public.agrinex_profiles (
        id, phone, full_name, role, farm_name, farmer_status
    )
    values (
        new.id, new.phone, profile_name, requested_role, profile_farm,
        case when requested_role = 'farmer' then 'pending' else 'not_applicable' end
    )
    on conflict (id) do nothing;

    return new;
end;
$$;

revoke all on function private.handle_agrinex_user_created()
    from public, anon, authenticated;

drop trigger if exists on_agrinex_auth_user_created on auth.users;
create trigger on_agrinex_auth_user_created
after insert on auth.users
for each row execute function private.handle_agrinex_user_created();
