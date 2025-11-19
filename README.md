# NBA Ingestion Project

A cohesive ingestion stack for NBA analytics. The repository unifies multiple public APIs—Ball Don't Lie, `nba_api`, SportsGameOdds, The Odds API, and BetsAPI—into a single PostgreSQL schema so every team, player, game, and odds snapshot references shared IDs. 

The stack is designed to:

* Backfill schedules, box scores, advanced stats, and play-by-play.
* Ingest team moneyline/spread/total odds plus player props from multiple providers.
* Keep everything in a single, canonical warehouse schema that is safe to re-run and extend.

---

## Repository layout

High-level structure:

README.md

alembic/

* env.py                – Alembic config wired to the `nba_ingest` package and settings
* script.py.mako        – Migration file template (used by `alembic revision`)
* versions/             – Schema migrations (run via `alembic upgrade head`), including a single baseline `*_initial_schema.py`

nba_ingest/

* .env                  – Local environment (DATABASE_URL, API keys). Not committed.
* requirements.txt      – Python dependencies for the ingestion stack
* nba_ingest/

  * balldontlie_ingest.py      – Teams/games/stats ETL (Ball Don’t Lie)
  * nba_api_ingest_pbp.py      – Game ID mapping + play-by-play ETL (`nba_api`)
  * unified_odds_ingest.py     – Orchestrator for BetsAPI + SportsGameOdds + The Odds API
  * props_hybrid_ingest.py     – Resumable hybrid props worker (SGO + The Odds API)
  * sgo_ingest.py              – Standalone SportsGameOdds loader
  * odds_api_ingest.py         – Standalone The Odds API loader
  * models.py                  – Core SQLAlchemy schema (teams, games, players, stats, odds)
  * props_models.py            – Props-specific schema (`props_players`, `props`, checkpoints)
  * normalization.py           – Name cleaning + mapping helpers
  * sports_game_odds_client.py – Typed SGO client with rate limiting
  * odds_api_client.py         – Typed The Odds API client with rate limiting
  * odds_helpers.py            – Helpers for standardizing odds, markets, line keys
  * config.py                  – Settings loader (env + `.env` via `python-dotenv`)

nba_pbp_ingest.py       – Optional standalone parquet writer using `nba_api`

reset_schema.py         – Dev helper script to drop/recreate the `public` schema and reapply migrations

---

## Setup

### 1. Python environment

Create and activate a virtual environment at the repo root, then install dependencies from `nba_ingest/requirements.txt`.

Windows (PowerShell):

* python -m venv .venv
* .venv\Scripts\Activate.ps1
* python -m pip install -r nba_ingest\requirements.txt

macOS / Linux:

* python3 -m venv .venv
* source .venv/bin/activate
* pip install -r nba_ingest/requirements.txt

---

### 2. Environment variables

Configuration is loaded via `nba_ingest.config.load_settings()`, which reads from:

* Environment variables, and
* The `nba_ingest/.env` file (using `python-dotenv`), if present.

Typical `.env` values:

DATABASE_URL=postgres://user:password@host:5432/dbname

# Ball Don't Lie / nba_api

BALDONTLIE_API_KEY=your_bdl_key_here
NBA_SEASONS=2021,2022,2023,2024

# SportsGameOdds (player props < 2023-05-03)

SPORTSGAMEODDS_API_KEY=your_sgo_key
SGO_BASE_URL=[https://api.sportsgameodds.com](https://api.sportsgameodds.com)
SGO_OBJECTS_PER_MONTH=2500
SGO_REQS_PER_MIN=10
SGO_INCLUDE_ALT_LINES=false
TOS_ACK_SINGLE_ACCOUNT=true

# The Odds API (player props >= 2023-05-03)

ODDS_API_KEY=your_the_odds_api_key
ODDS_API_BASE_URL=[https://api.the-odds-api.com/v4](https://api.the-odds-api.com/v4)
ODDS_REQS_PER_MIN=30
PROP_MARKETS=player_points,player_assists,player_rebounds,player_threes
REGION=us

# BetsAPI (team odds full window)

BETSAPI_API_KEY=your_betsapi_key

# Hybrid ingest defaults

HISTORICAL_START=2021-10-19
HISTORICAL_SPLIT=2023-05-03
HISTORICAL_END=2025-11-11
DRY_RUN=false
GLOBAL_MAX_EVENTS=7000

Notes:

* `.env` is for local development only and should **not** be committed to Git.
* If this repo is public, rotate any secrets that were ever committed before you added `.gitignore`.

---

### 3. Database schema and Alembic migrations

The schema is managed by Alembic. The key pieces:

* `alembic/env.py` imports:

  * `nba_ingest.models.Base.metadata` (core schema)
  * `nba_ingest.props_models.PropsBase.metadata` (props-specific schema)
* `alembic/script.py.mako` is the template Alembic uses when generating new migrations.
* `alembic/versions/<hash>_initial_schema.py` is the baseline migration, created after we “nuked” the old chain and unified the schema.

To apply the schema to your Postgres database:

* python -m alembic upgrade head

This will create all tables in the `public` schema of the database pointed at by `DATABASE_URL`.

#### Making schema changes

If you change `models.py` or `props_models.py` and want to evolve the DB:

1. Make your model changes.

2. Generate a migration:

   * python -m alembic revision --autogenerate -m "describe your change"

3. Apply it:

   * python -m alembic upgrade head

Alembic will diff the current DB schema versus your SQLAlchemy metadata and generate upgrade/downgrade operations.

---

### 4. Dev helper: resetting the schema (nuke & pave)

For local development, you may want to completely reset your Postgres schema and reapply migrations. `reset_schema.py` does that for the `public` schema:

* Drops `public` with `CASCADE`
* Recreates `public`
* Restores basic grants

Usage (dev only – destructive):

* python reset_schema.py
* python -m alembic upgrade head

Do **not** run this against any environment you care about; it will wipe all tables in the `public` schema.

---

## Canonical schema highlights

Core warehouse entities (from `models.py` and `props_models.py`):

* leagues – High-level metadata (NBA, etc.) and provider sport keys.
* teams – Canonical team entities, with normalized names and JSON maps of provider IDs (`provider_team_ids`).
* players – Canonical players with normalized names, provider IDs, and basic bio info.
* games – One row per NBA game, with home/away foreign keys, season info, provider event IDs, and date/time.
* ingestion_state / ingestion_runs / checkpoints – Tables for tracking resumable ingestion jobs per provider and date.
* odds_books – Bookmaker metadata (name, provider key, etc.).
* odds_markets / game_odds (depending on naming in your version) – Unified table(s) for team odds and props (provider, bookmaker, market type, participant type, line, price, timestamp).
* play_by_play – Full nba_api play-by-play logs keyed by game and event index.
* player_game_stats – Traditional box score stats keyed by game + player.
* player_game_advanced – Advanced stats keyed by game + player.
* props_players – Props-specific player identity table, separate from the canonical `players` table to avoid duplicate metadata conflicts.
* props – Individual player props (points, assists, rebounds, threes, etc.), keyed to `props_players` and games.

All providers write into this shared schema so rows from different APIs reference the same `teams`, `players`, and `games` wherever possible. Props are separated into `props_players` / `props` to keep their life cycle independent while still linking to canonical games.

---

## How to “start the machine” (end-to-end ingestion)

Once your DB schema is applied and env vars are set, you can run the ingestion pipelines.

### Removing previously ingested games for one team

If you need to wipe Ball Don't Lie data for a single team before re-ingesting (e.g., Denver after fixing the `bdl_team_id`), run the helper SQL script against Postgres:

```
psql $DATABASE_URL -f sql/delete_team_games.sql
```

The script deletes play-by-play, box scores, odds, and the `games` rows for the chosen team and date window (defaults cover Denver from 2021-10-01 through 2026-06-30). Edit the `\set` variables at the top of `sql/delete_team_games.sql` to target a different team or date range.

### Export a week of provider games to CSV (ID comparison)

Generate CSV snapshots of a week's games from each provider to compare provider IDs/abbreviations against your server:

```
# Dump one CSV per provider into ./reports for the selected week
python -m nba_ingest.export_games_csv --start 2024-10-01 --days 7

# Single provider with custom path
python -m nba_ingest.export_games_csv --provider balldontlie --start 2024-10-01 --days 7 --output /tmp/bdl_week.csv
```

Providers covered:

* `balldontlie` – uses `BALDONTLIE_API_KEY`
* `odds_api` – uses `ODDS_API_KEY` (and optional `ODDS_API_BASE_URL`)
* `sgo` – uses `SPORTSGAMEODDS_API_KEY` (and optional `SGO_BASE_URL`)
* `betsapi` – uses `BETSAPI_API_KEY` (and optional `BETSAPI_BASE_URL`)
* `unified_odds` – reads existing `games` from `DATABASE_URL` with provider IDs

Each CSV includes provider event IDs, home/away names/abbreviations, and provider team IDs (when available) so you can line them up against your own mappings.

### 1. Ball Don't Lie schedules + stats

Populates leagues, teams, games, and player-level stats:

* python -m nba_ingest.balldontlie_ingest --start 2021-10-19 --end 2025-11-11

This script:

* Syncs `teams`, `games`, `player_game_stats`, and `player_game_advanced`.
* Resolves entities by provider IDs first, then falls back to normalized names.
* Is idempotent (safe to re-run).

### 2. nba_api mapping + play-by-play

Maps `nba_game_id` onto canonical games and loads full play-by-play logs:

* python -m nba_ingest.nba_api_ingest_pbp --start 2021-10-19 --end 2025-11-11 --delay 1.0

This script:

* Finds each game in `games` via home/away teams + date.
* Fills in `nba_game_id`.
* Writes play-by-play rows keyed by `(game_id, event_num)`.
* Uses a delay to respect `nba_api`’s informal rate limits.

### 3. Team odds + player props (unified orchestrator)

Runs BetsAPI, SportsGameOdds, and The Odds API in one shot, with resumable checkpoints:

* python -m nba_ingest.unified_odds_ingest --start 2021-10-19 --end 2025-11-11

Internally, the orchestrator:

1. Runs BetsAPI for team markets (moneyline, spread, totals) across the full window.
2. Uses SportsGameOdds for player props from 2021-10-19 to 2023-05-02.
3. Uses The Odds API for player props from 2023-05-03 onward.

It records progress per provider/date in `ingestion_state` and related tables, so re-running will continue from the next incomplete checkpoint instead of starting over.

---

## Unified ETL modules (per provider)

Each provider also has its own dedicated module if you want to run things more granularly.

1. Ball Don't Lie (teams, games, stats)

   * python -m nba_ingest.balldontlie_ingest
     Syncs core team/game/player box score and advanced stats into the canonical schema.

2. NBA API scoreboard + play-by-play

   * python -m nba_ingest.nba_api_ingest_pbp --start 2021-10-19 --end 2025-11-11 --delay 1.0

3. The Odds API team odds + player props

   * python -m nba_ingest.odds_api_ingest --start 2023-05-03 --end 2023-06-30 --markets player_points,player_assists

   Historical snapshots are taken at a canonical timestamp (default 12:00:00Z). Results are normalized into `games`, odds books/markets, and props tables.

4. SportsGameOdds player props

   * python -m nba_ingest.sgo_ingest --start 2021-10-19 --end 2023-05-02 --markets player_points,player_rebounds

   This maps SGO events to `games`, writes provider event IDs, and stores props snapshots in the unified odds/props tables (or directly in `props` depending on the version you’re running).

5. Hybrid props worker

   * python -m nba_ingest.props_hybrid_ingest --start 2021-10-19 --end 2025-11-11 --markets player_points,player_assists,player_rebounds,player_threes --region us --resume true

   This is a dedicated, resumable worker for player props that splits the date range at 2023-05-03 and uses SGO vs The Odds API accordingly.

---

## Hybrid player props ingestion details

The hybrid props pipeline combines:

* SportsGameOdds (SGO):

  * Coverage: 2021-10-19 → 2023-05-02
  * Rate limit: 10 requests/minute
  * Quota: ~2,500 events/month on free tier
* The Odds API:

  * Coverage: 2023-05-03 → 2025-11-11
  * Rate limit: 30 requests/minute

Key behavior:

* Dates before 2023-05-03 are handled by SGO; dates on or after 2023-05-03 are handled by The Odds API.
* Checkpoints are stored per provider/date so you can stop and resume runs with `--resume true`.
* Rate limits are enforced in the client code with retries for 429/5xx responses.
* Missing markets or books are treated as warnings, not hard failures, to keep the pipeline moving.

Props live in dedicated tables:

* `props_players` – Identity table for props-specific players.
* `props` – Individual player props rows, with foreign keys to `props_players` and games.
* Checkpoint and ingestion run tables – Track discovery and processing status for resumability.

---

## Provider coverage (high-level)

Sports betting data from three providers lands in the unified schema:

* SportsGameOdds

  * Scope: Player props (points, assists, rebounds, threes)
  * Window: 2021-10-19 → 2023-05-02
  * Limits: 10 req/min, 2,500 events/month

* The Odds API

  * Scope: Player props (points, assists, rebounds, threes)
  * Window: 2023-05-03 → 2025-11-11
  * Limits: 30 req/min

* BetsAPI

  * Scope: Team moneyline, spreads, totals
  * Window: 2021-10-19 → 2025-11-11
  * Limits: Provider-specific, conservatively throttled in code (~1–2 req/sec)

Each loader updates `ingestion_state` and related tables so you always know which dates/providers are complete and which still need work.

---

## Testing and validation

Run tests from the `nba_ingest` directory:

* cd nba_ingest
* pytest

For ad-hoc validation in SQL, you can join games, stats, play-by-play, and odds. Example pattern:

* Count PBP events per game.
* Count number of players with stats per game.
* Count odds rows per game for a given provider/market.

This helps you sanity-check data coverage before building downstream models.

---

## Logging and operations

* CLI commands log:

  * Number of events/games fetched
  * Number of odds/props rows written
  * HTTP errors and retry behavior
  * Current rate-limit pacing (especially for SGO and The Odds API)

* Checkpoint tables (`checkpoints`, `ingestion_runs`, `ingestion_state`) let you:

  * Resume after failures or API quota resets.
  * Inspect which dates/providers were processed successfully.
  * Limit backfills via configs like `GLOBAL_MAX_EVENTS`.

Operational tips:

* Start with a small date range (e.g., one week) when wiring up a new provider or market set.
* For SGO free tier, monitor monthly usage and use `--resume true` after the quota resets.
* Use `reset_schema.py` only in development when you want a clean slate; always follow it with `python -m alembic upgrade head`.

---

## Local dev workflow (summary)

1. Clone repo.
2. At repo root:

   * python -m venv .venv
   * ..venv\Scripts\Activate.ps1 (on Windows) or source .venv/bin/activate (macOS/Linux)
   * python -m pip install -r nba_ingest\requirements.txt
3. Create `nba_ingest/.env` with `DATABASE_URL` and API keys.
4. Apply migrations:

   * python -m alembic upgrade head
5. Run ingestion for a small test range (e.g., 7 days) to validate.
6. For schema resets in dev:

   * python reset_schema.py
   * python -m alembic upgrade head
