-- OffsAid cross-case linking: database schema (Postgres 14+; bit_count needs 14).
-- Needs the pg_trgm extension (ships with Postgres contrib). pgvector is NOT needed for this version.
-- Convert this to the repo's migration tool (Alembic, Flyway, plain SQL migrations...) rather than running it by hand.

create extension if not exists pg_trgm;

create table if not exists cases (
  id             bigserial primary key,
  external_ref   text unique,                          -- e.g. the WhatsApp message id; makes webhook retries idempotent
  created_at     timestamptz not null default now(),
  source         text not null check (source in ('live','kefwa_past','public_warning','simulated')),
  status         text not null default 'unreviewed'
                 check (status in ('unreviewed','reported','confirmed_scam','verified_genuine')),
  status_updated_at timestamptz,
  claimed_club   text,                                 -- as written in the offer, plain text
  claimed_agent  text,
  source_url     text,                                 -- required for public_warning rows
  summary        jsonb not null default '{}'::jsonb    -- extracted fields and check results, no player identity
);

-- One row per identifier seen in a case. The raw value is NEVER stored, only a keyed hash.
create table if not exists identifiers (
  id          bigserial primary key,
  case_id     bigint not null references cases(id) on delete cascade,
  kind        text not null check (kind in ('phone','account','email','domain')),
  value_hash  text not null,                           -- HMAC-SHA256(secret key, kind + ':' + normalised value)
  role        text                                     -- agent_contact | payment_to | sender
);
create index if not exists identifiers_lookup on identifiers (kind, value_hash);
create index if not exists identifiers_case on identifiers (case_id);

-- Fingerprints of letters and of the agent's wording. The file itself is NOT stored.
create table if not exists documents (
  id           bigserial primary key,
  case_id      bigint not null references cases(id) on delete cascade,
  kind         text not null check (kind in ('letter','wording')),
  phash        bigint,                                 -- 64-bit perceptual hash of page 1 (letters only), signed
  masked_text  text not null,                          -- names, dates, amounts and contacts replaced by placeholders
  labels       text[] not null default '{}'            -- scam-language labels found in this case (wording rows)
);
create index if not exists documents_text_trgm on documents using gin (masked_text gin_trgm_ops);
create index if not exists documents_case on documents (case_id);
