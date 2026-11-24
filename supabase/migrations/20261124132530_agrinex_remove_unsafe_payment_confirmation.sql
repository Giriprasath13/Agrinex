-- Remove the earlier authenticated-client overload. It allowed users to claim payment success.
drop function if exists public.confirm_agrinex_razorpay_payment(uuid,text,text);
