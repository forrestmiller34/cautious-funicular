-- Remove all BallDon'tLie-derived data for a specific team and date range.
--
-- Usage:
--   1) Connect to Postgres (e.g., `psql $DATABASE_URL`).
--   2) SET the bounds you want; defaults below match Denver 2021-10-01 through 2026-06-30.
--   3) \i sql/delete_team_games.sql
--
-- The script deletes dependent tables first to satisfy foreign-key constraints,
-- then deletes the games themselves. Adjust the abbreviation and dates as needed.

-- ===== Editable inputs =====
-- Team match that should be removed
\set team_abbrev 'DEN'
\set team_bdl_id 8
-- Date window (inclusive) for the games you want to purge
\set start_date '2021-10-01'
\set end_date   '2026-06-30'
-- ==========================

BEGIN;

WITH target_team AS (
    SELECT id
    FROM teams
    WHERE abbreviation = :'team_abbrev'
       OR bdl_team_id = :'team_bdl_id'
    LIMIT 1
),
team_games AS (
    SELECT g.id
    FROM games g
    JOIN target_team t ON g.home_team_id = t.id OR g.away_team_id = t.id
    WHERE g.game_date BETWEEN :'start_date'::date AND :'end_date'::date
)
DELETE FROM play_by_play pbp USING team_games tg WHERE pbp.game_id = tg.id;

WITH team_games AS (
    SELECT g.id
    FROM games g
    JOIN target_team t ON g.home_team_id = t.id OR g.away_team_id = t.id
    WHERE g.game_date BETWEEN :'start_date'::date AND :'end_date'::date
)
DELETE FROM player_game_stats s USING team_games tg WHERE s.game_id = tg.id;

WITH team_games AS (
    SELECT g.id
    FROM games g
    JOIN target_team t ON g.home_team_id = t.id OR g.away_team_id = t.id
    WHERE g.game_date BETWEEN :'start_date'::date AND :'end_date'::date
)
DELETE FROM player_game_advanced a USING team_games tg WHERE a.game_id = tg.id;

WITH team_games AS (
    SELECT g.id
    FROM games g
    JOIN target_team t ON g.home_team_id = t.id OR g.away_team_id = t.id
    WHERE g.game_date BETWEEN :'start_date'::date AND :'end_date'::date
)
DELETE FROM odds_ingest_jobs o USING team_games tg WHERE o.game_id = tg.id;

WITH team_games AS (
    SELECT g.id
    FROM games g
    JOIN target_team t ON g.home_team_id = t.id OR g.away_team_id = t.id
    WHERE g.game_date BETWEEN :'start_date'::date AND :'end_date'::date
)
DELETE FROM game_odds o USING team_games tg WHERE o.game_id = tg.id;

WITH team_games AS (
    SELECT g.id
    FROM games g
    JOIN target_team t ON g.home_team_id = t.id OR g.away_team_id = t.id
    WHERE g.game_date BETWEEN :'start_date'::date AND :'end_date'::date
)
DELETE FROM unified_game_odds u USING team_games tg WHERE u.game_id = tg.id;

WITH team_games AS (
    SELECT g.id
    FROM games g
    JOIN target_team t ON g.home_team_id = t.id OR g.away_team_id = t.id
    WHERE g.game_date BETWEEN :'start_date'::date AND :'end_date'::date
)
DELETE FROM odds_markets m USING team_games tg WHERE m.event_id = tg.id;

WITH team_games AS (
    SELECT g.id
    FROM games g
    JOIN target_team t ON g.home_team_id = t.id OR g.away_team_id = t.id
    WHERE g.game_date BETWEEN :'start_date'::date AND :'end_date'::date
)
DELETE FROM sdv_game_map m USING team_games tg WHERE m.internal_game_id = tg.id;

-- Finally remove the games themselves
WITH team_games AS (
    SELECT g.id
    FROM games g
    JOIN target_team t ON g.home_team_id = t.id OR g.away_team_id = t.id
    WHERE g.game_date BETWEEN :'start_date'::date AND :'end_date'::date
)
DELETE FROM games g USING team_games tg WHERE g.id = tg.id;

-- Clear ingestion markers for the affected seasons (Balldontlie uses data_type 'stats')
DELETE FROM ingestion_status
WHERE source = 'balldontlie'
  AND season BETWEEN 2021 AND 2025
  AND data_type = 'stats';

COMMIT;

-- Run \echo statements to confirm counts after execution if desired, e.g.:
-- SELECT COUNT(*) FROM games g JOIN teams t ON t.id = g.home_team_id OR t.id = g.away_team_id WHERE t.abbreviation = :'team_abbrev';
