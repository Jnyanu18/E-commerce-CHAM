-- 0010: city tier reference data.
-- SOURCE / ASSUMPTION (not authoritative): there is no official "D2C city tier" list. This is a working
-- classification informed by the Government of India HRA X/Y/Z city classes and common Indian e-commerce
-- practice, adjusted so the cities in engine/seed/synthetic.py carry the same tier there as here
-- (e.g. Pune tier_1, Jaipur/Lucknow/Indore tier_2, Patna/Guwahati tier_3). Delhi-NCR and Mumbai-region
-- satellite towns are placed in 'metro' as a business choice. The brand/ops team should review and amend;
-- rows are plain data (upsert by city) and can be changed without schema changes.
-- Keys are stored in canonical display form; use lookup_city_tier() for case/whitespace-insensitive lookup.

insert into city_tiers (city, tier) values
  -- metro
  ('Mumbai','metro'),('Delhi','metro'),('New Delhi','metro'),('Bengaluru','metro'),('Bangalore','metro'),
  ('Hyderabad','metro'),('Chennai','metro'),('Kolkata','metro'),('Gurugram','metro'),('Gurgaon','metro'),
  ('Noida','metro'),('Navi Mumbai','metro'),('Thane','metro'),
  -- tier_1
  ('Pune','tier_1'),('Ahmedabad','tier_1'),('Surat','tier_1'),('Chandigarh','tier_1'),('Kochi','tier_1'),
  ('Coimbatore','tier_1'),('Vadodara','tier_1'),('Ghaziabad','tier_1'),('Faridabad','tier_1'),
  -- tier_2
  ('Jaipur','tier_2'),('Lucknow','tier_2'),('Indore','tier_2'),('Nagpur','tier_2'),('Bhopal','tier_2'),
  ('Visakhapatnam','tier_2'),('Kanpur','tier_2'),('Thiruvananthapuram','tier_2'),('Nashik','tier_2'),
  ('Vijayawada','tier_2'),('Mysuru','tier_2'),('Mysore','tier_2'),('Ludhiana','tier_2'),('Agra','tier_2'),
  ('Rajkot','tier_2'),('Varanasi','tier_2'),('Madurai','tier_2'),('Meerut','tier_2'),('Raipur','tier_2'),
  ('Dehradun','tier_2'),('Amritsar','tier_2'),('Bhubaneswar','tier_2'),('Mangaluru','tier_2'),
  -- tier_3
  ('Patna','tier_3'),('Guwahati','tier_3'),('Ranchi','tier_3'),('Jodhpur','tier_3'),('Gwalior','tier_3'),
  ('Jabalpur','tier_3'),('Prayagraj','tier_3'),('Allahabad','tier_3'),('Srinagar','tier_3'),('Jammu','tier_3'),
  ('Shimla','tier_3'),('Udaipur','tier_3'),('Kota','tier_3'),('Siliguri','tier_3'),('Dhanbad','tier_3'),
  ('Jamshedpur','tier_3'),('Hubballi','tier_3'),('Salem','tier_3'),('Tiruchirappalli','tier_3'),
  ('Aurangabad','tier_3'),('Solapur','tier_3'),('Gorakhpur','tier_3'),('Bareilly','tier_3')
on conflict (city) do update set tier = excluded.tier;

-- Case/whitespace-insensitive lookup; unknown cities map to 'unknown' (never NULL).
create function lookup_city_tier(p_city text) returns text
language sql stable set search_path = public as $$
  select coalesce(
    (select tier from city_tiers where lower(city) = lower(btrim(p_city)) limit 1),
    'unknown');
$$;
