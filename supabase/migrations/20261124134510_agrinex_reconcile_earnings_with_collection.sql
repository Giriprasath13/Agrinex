-- Earnings only become settlement-eligible after confirmed collection.
create or replace view public.agrinex_farmer_earnings
with (security_invoker=true)
as
select
  i.farmer_id,
  date_trunc('day',i.created_at)::date as earning_date,
  count(*) as order_item_count,
  sum(i.line_total) as gross_sales,
  sum(i.platform_fee) as platform_fees,
  sum(i.delivery_fee) as delivery_fees,
  sum(case
    when (o.payment_method='razorpay' and o.payment_status='captured')
      or (o.payment_method='cash_on_delivery' and o.payment_status='collected')
    then i.farmer_payout else 0
  end) as estimated_farmer_payout,
  sum(case
    when ((o.payment_method='razorpay' and o.payment_status='captured')
      or (o.payment_method='cash_on_delivery' and o.payment_status='collected'))
      and i.payout_status='settled'
    then i.farmer_payout else 0
  end) as settled_amount,
  sum(case
    when ((o.payment_method='razorpay' and o.payment_status='captured')
      or (o.payment_method='cash_on_delivery' and o.payment_status='collected'))
      and i.payout_status<>'settled'
    then i.farmer_payout else 0
  end) as unsettled_amount,
  sum(case
    when not (
      (o.payment_method='razorpay' and o.payment_status='captured')
      or (o.payment_method='cash_on_delivery' and o.payment_status='collected')
    )
    then i.farmer_payout else 0
  end) as awaiting_collection_amount
from public.agrinex_order_items i
join public.agrinex_orders o on o.id=i.order_id
where i.status='delivered'
group by i.farmer_id,date_trunc('day',i.created_at)::date;
revoke all on public.agrinex_farmer_earnings from anon,authenticated;
grant select on public.agrinex_farmer_earnings to authenticated;
