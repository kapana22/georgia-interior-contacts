-- Rotating the full compressed checkpoint can exceed the service role's
-- inherited API timeout. Keep the exception scoped to this server-only RPC.
alter function public.publish_company_directory(text,text,text,integer,timestamptz,jsonb)
  set statement_timeout = '60s';

notify pgrst, 'reload schema';
