-- AgriNex authentication profile schema.
-- Apply only to the dedicated AgriNex Supabase project, never to another app's database.

create table if not exists public.agrinex_profiles (
    id uuid primary key references auth.users (id) on delete cascade,
    phone text not null unique,
    full_name text not null,
    role text not null check (role in ('customer', 'farmer')),
    farm_name text,
    farmer_status text not null default 'not_applicable'
        check (farmer_status in ('not_applicable', 'pending', 'approved', 'rejected')),
    created_at timestamptz not null default now(),
    constraint agrinex_farmer_farm_name_required
        check (role <> 'farmer' or nullif(trim(farm_name), '') is not null)
);

alter table public.agrinex_profiles enable row level security;

revoke all on table public.agrinex_profiles from anon, authenticated;
grant select on table public.agrinex_profiles to authenticated;

drop policy if exists "Users can read their own AgriNex profile"
    on public.agrinex_profiles;

create policy "Users can read their own AgriNex profile"
    on public.agrinex_profiles
    for select
    to authenticated
    using ((select auth.uid()) = id);

-- Trigger functions run with their owner's privileges. Keep this function
-- outside exposed schemas and revoke direct execution from API roles.
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
    -- role is an explicit, validated customer/farmer choice from signup.
    -- Farmer privileges remain disabled until farmer_status is approved.
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
        id,
        phone,
        full_name,
        role,
        farm_name,
        farmer_status
    )
    values (
        new.id,
        new.phone,
        profile_name,
        requested_role,
        profile_farm,
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
for each row
execute function private.handle_agrinex_user_created();
