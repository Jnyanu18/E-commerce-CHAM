-- 0006: pincode stays text (leading zeros survive); when present it must be exactly 6 digits.
alter table orders
  add constraint orders_pincode_format check (pincode is null or pincode ~ '^[0-9]{6}$');
