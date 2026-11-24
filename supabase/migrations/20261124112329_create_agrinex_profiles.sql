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
