-- OffsAid: referrals from the chat summary go to the same KEFWA review queue.
-- Team decision (2026-11-08): when a case is referred, the player's WhatsApp number is shared with KEFWA
-- so KEFWA can contact them. Players are told this in the welcome message and again when referred.
-- The number is stored ONLY here (never in cases/identifiers) and is removed with the queue row.

alter table review_queue alter column case_id drop not null;   -- an agent or message check may have no stored case
alter table review_queue add column if not exists origin text not null default 'linking'
  check (origin in ('linking', 'chat_summary'));
alter table review_queue add column if not exists player_contact text;   -- raw number, for KEFWA to call back
alter table review_queue add column if not exists findings_text text;    -- the summary the player was sent
