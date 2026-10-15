-- 0005: canonical city tier values: metro | tier_1 | tier_2 | tier_3 | unknown  (docs/CONTRACTS.md section 8).
-- Kept as text + CHECK (not a Postgres ENUM type) so values can be extended with a plain constraint swap.
-- Data-preserving: old tier1/2/3 values are rewritten, nothing is dropped.

alter table orders      drop constraint orders_city_tier_check;
alter table city_tiers  drop constraint city_tiers_tier_check;

update orders     set city_tier = replace(city_tier, 'tier', 'tier_') where city_tier in ('tier1','tier2','tier3');
update city_tiers set tier      = replace(tier,      'tier', 'tier_') where tier      in ('tier1','tier2','tier3');
update orders     set city_tier = 'unknown' where city_tier is null;

alter table orders
  add constraint orders_city_tier_check check (city_tier in ('metro','tier_1','tier_2','tier_3','unknown')),
  alter column city_tier set default 'unknown',
  alter column city_tier set not null;
alter table city_tiers
  add constraint city_tiers_tier_check check (tier in ('metro','tier_1','tier_2','tier_3','unknown'));

-- Writers that send an explicit NULL still get a valid value instead of an error.
create function orders_city_tier_default() returns trigger language plpgsql
set search_path = public as $$
begin
  new.city_tier := coalesce(new.city_tier, 'unknown');
  return new;
end $$;
create trigger orders_city_tier_default before insert or update of city_tier on orders
  for each row execute function orders_city_tier_default();
