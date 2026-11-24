-- Farmer earnings reflect fulfilled produce only. They are estimates, not bank settlements.
create or replace view public.agrinex_farmer_earnings
with (security_invoker=true)
as
select farmer_id,date_trunc('day',created_at)::date as earning_date,
       count(*) as order_item_count,
       sum(line_total) as gross_sales,
       sum(platform_fee) as platform_fees,
       sum(delivery_fee) as delivery_fees,
       sum(farmer_payout) as estimated_farmer_payout,
       sum(case when payout_status='settled' then farmer_payout else 0 end) as settled_amount,
       sum(case when payout_status<>'settled' then farmer_payout else 0 end) as unsettled_amount
from public.agrinex_order_items
where status='delivered'
group by farmer_id,date_trunc('day',created_at)::date;
revoke all on public.agrinex_farmer_earnings from anon,authenticated;
grant select on public.agrinex_farmer_earnings to authenticated;
