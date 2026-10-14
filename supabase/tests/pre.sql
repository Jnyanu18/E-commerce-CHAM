-- Emulates Supabase's default privileges and service_role on plain PostgreSQL. Apply AFTER local_stub.sql and
-- BEFORE the migrations, so tables created by migrations get the same default grants Supabase gives them.
-- This is an EMULATION: real Supabase behaviour (JWT -> auth.uid(), PostgREST, role switching) is unverified here.
do $$ begin
  create role service_role nologin bypassrls; exception when duplicate_object then null; end $$;

grant usage on schema public to anon, authenticated, service_role;
grant usage on schema auth to anon, authenticated, service_role;
alter default privileges in schema public grant all on tables    to anon, authenticated, service_role;
alter default privileges in schema public grant all on sequences to anon, authenticated, service_role;
alter default privileges in schema public grant all on functions to anon, authenticated, service_role;
