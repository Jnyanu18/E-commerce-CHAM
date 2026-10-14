-- City tier seed and lookup. Read-only.
select test_h.ok((select count(*) from city_tiers) >= 50, 'city_tiers seeded');
select test_h.ok(not exists (select 1 from city_tiers where tier not in ('metro','tier_1','tier_2','tier_3','unknown')), 'only canonical tiers in seed');
-- every city used by engine/seed/synthetic.py, with the tier that file uses (renamed to the canonical enum)
select test_h.ok((select array_agg(city order by city) from city_tiers where (city, tier) in (
  ('Mumbai','metro'),('Delhi','metro'),('Bengaluru','metro'),('Hyderabad','metro'),('Pune','tier_1'),('Jaipur','tier_2'),
  ('Lucknow','tier_2'),('Indore','tier_2'),('Patna','tier_3'),('Guwahati','tier_3'))) is not null
  and (select count(*) from city_tiers where city in ('Mumbai','Delhi','Bengaluru','Hyderabad','Pune','Jaipur','Lucknow','Indore','Patna','Guwahati')
       and (city, tier) in (('Mumbai','metro'),('Delhi','metro'),('Bengaluru','metro'),('Hyderabad','metro'),('Pune','tier_1'),
       ('Jaipur','tier_2'),('Lucknow','tier_2'),('Indore','tier_2'),('Patna','tier_3'),('Guwahati','tier_3'))) = 10,
  'all 10 synthetic seed cities present with matching tiers');
select test_h.ok(lookup_city_tier('  pUNE ') = 'tier_1', 'lookup is case/space insensitive');
select test_h.ok(lookup_city_tier('Atlantis') = 'unknown', 'unknown city -> unknown');
select test_h.ok(lookup_city_tier(null) = 'unknown', 'null city -> unknown');
select test_h.ok(lookup_city_tier('') = 'unknown', 'empty city -> unknown');
select 'PASS 06_city_tiers' as result;
