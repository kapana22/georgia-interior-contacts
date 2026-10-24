-- Public business directory; collector checkpoints remain server-only.
begin;
create table public.companies (
  id text primary key,
  record jsonb not null check (record->>'id' = id),
  content_hash text not null,
  active boolean not null default true
);
create index companies_active_id on public.companies (id) where active;
create index companies_tax_id on public.companies ((record->>'tax_id'));
create table public.directory_meta (
  id text primary key check (id = 'current'),
  status text not null check (status in ('publishing','ready')),
  revision text not null,
  total integer not null,
  updated_at timestamptz not null,
  report jsonb not null default '{}'
);
create table public.collector_checkpoints (
  id text primary key check (id in ('current','previous')),
  archive text not null,
  sha256 text not null,
  revision text not null
);
alter table public.companies enable row level security;
alter table public.directory_meta enable row level security;
alter table public.collector_checkpoints enable row level security;
revoke all on public.companies, public.directory_meta, public.collector_checkpoints from anon, authenticated;
grant select on public.companies, public.directory_meta to anon, authenticated;
grant all on public.companies, public.directory_meta, public.collector_checkpoints to service_role;
create policy directory_read on public.companies for select to anon, authenticated using (active);
create policy directory_meta_read on public.directory_meta for select to anon, authenticated using (true);

-- A single transaction advances both the durable cursor and the visible version.
create function public.publish_company_directory(p_archive text, p_sha256 text, p_revision text, p_total integer, p_updated_at timestamptz, p_report jsonb)
returns void language plpgsql security invoker set search_path = '' as $$
begin
  if (select count(*) from public.companies where active) <> p_total then
    raise exception 'Directory count mismatch';
  end if;
  insert into public.collector_checkpoints(id,archive,sha256,revision)
    select 'previous',archive,sha256,revision from public.collector_checkpoints where id='current'
    on conflict(id) do update set archive=excluded.archive,sha256=excluded.sha256,revision=excluded.revision;
  insert into public.collector_checkpoints values ('current',p_archive,p_sha256,p_revision)
    on conflict(id) do update set archive=excluded.archive,sha256=excluded.sha256,revision=excluded.revision;
  insert into public.directory_meta values ('current','ready',p_revision,p_total,p_updated_at,p_report)
    on conflict(id) do update set status='ready',revision=excluded.revision,total=excluded.total,updated_at=excluded.updated_at,report=excluded.report;
end;
$$;
revoke all on function public.publish_company_directory(text,text,text,integer,timestamptz,jsonb) from public, anon, authenticated;
grant execute on function public.publish_company_directory(text,text,text,integer,timestamptz,jsonb) to service_role;
commit;
