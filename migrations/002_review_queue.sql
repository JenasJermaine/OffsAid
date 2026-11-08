-- OffsAid KEFWA review queue: cases that cross-case linking escalated (see docs/CROSS_CASE_LINKING_GUIDE.md, step 7).
-- One row per escalated case. Erasing the case (delete_case) removes its queue row too.
-- linked_case_ids are internal ids for the reviewer; they are never shown to the player.

create table if not exists review_queue (
  id               bigserial primary key,
  created_at       timestamptz not null default now(),
  case_id          bigint not null unique references cases(id) on delete cascade,
  reasons          jsonb not null default '[]'::jsonb,   -- escalation_reasons from find_links
  linked_case_ids  bigint[] not null default '{}',       -- earlier cases that matched
  status           text not null default 'open' check (status in ('open','resolved')),
  reviewer_note    text not null default '',
  resolved_at      timestamptz
);
create index if not exists review_queue_open on review_queue (status, created_at);
