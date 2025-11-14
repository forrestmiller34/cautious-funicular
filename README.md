# NBA Ingestion Project

A cohesive ingestion stack for NBA analytics. The repository unifies multiple public APIs—Ball Don't Lie, nba_api, SportsGameOdds, The Odds API, and BetsAPI—into a single PostgreSQL schema so every team, player, game, and odds snapshot references shared IDs.

## Repository layout

```
README.md
alembic/
  └── versions/                 # Schema migrations (run via `alembic upgrade head`)
nba_ingest/
  ├── requirements.txt          # Python dependencies
  ├── nba_ingest/
  │   ├── balldontlie_ingest.py # Teams/games/stats ETL
  │   ├── nba_api_ingest_pbp.py # Game ID mapping + play-by-play ETL
  │   ├── unified_odds_ingest.py# BetsAPI + SGO + The Odds API orchestrator
  │   ├── props_hybrid_ingest.py# Resumable props worker (split SGO/Odds)
  │   ├── sgo_ingest.py         # Standalone SGO loader
  │   ├── odds_api_ingest.py    # Standalone Odds API loader
  │   ├── models.py             # SQLAlchemy schema definitions
  │   ├── normalization.py      # Name cleaning + mapping helpers
  │   ├── sports_game_odds_client.py / odds_api_client.py / odds_helpers.py
  │   └── ...                   # API server, feature pipeline, etc.
nba_pbp_ingest.py               # Standalone parquet writer using nba_api
```

## Prerequisites

1. **Python environment**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r nba_ingest/requirements.txt
   ```

2. **Environment variables**
   Define these in `.env` or your shell (all required unless noted). The orchestrators will exit with a clear error if any key is missing.

   ```env
   DATABASE_URL=postgres://user:password@host:5432/dbname

   # Ball Don't Lie / nba_api
   BALDONTLIE_API_KEY=...
   NBA_SEASONS=2021,2022,2023,2024          # optional override for BDL backfills

   # SportsGameOdds (player props < 2023-05-03)
   SPORTSGAMEODDS_API_KEY=...
   SGO_BASE_URL=https://api.sportsgameodds.com
   SGO_OBJECTS_PER_MONTH=2500
   SGO_REQS_PER_MIN=10
   SGO_INCLUDE_ALT_LINES=false
   TOS_ACK_SINGLE_ACCOUNT=true

   # The Odds API (player props >= 2023-05-03)
   ODDS_API_KEY=...
   ODDS_API_BASE_URL=https://api.the-odds-api.com/v4
   ODDS_REQS_PER_MIN=30
   PROP_MARKETS=player_points,player_assists,player_rebounds,player_threes
   REGION=us

   # BetsAPI (team odds full window)
   BETSAPI_API_KEY=...

   # Hybrid ingest defaults
   HISTORICAL_START=2021-10-19
   HISTORICAL_SPLIT=2023-05-03
   HISTORICAL_END=2025-11-11
   DRY_RUN=false
   GLOBAL_MAX_EVENTS=7000
   ```

3. **Database schema**
   Apply migrations before running any loaders:
   ```bash
   alembic upgrade head
   ```

## Canonical schema highlights

* **leagues, teams, players** – Canonical IDs with JSON `provider_*_ids` maps so every API response resolves to a single internal entity.
* **events (games)** – One row per NBA game with home/away team FKs, start time, season metadata, and provider event IDs.
* **player_game_stats / player_game_advanced** – Box score + advanced stats keyed by `(game_id, player_id)`.
* **play_by_play** – All nba_api events keyed by `(game_id, event_num)` with normalized team/player references.
* **odds_books / odds_markets** – Unified odds + props table storing provider, bookmaker, market type, participant, line/price, and ingestion timestamp.
* **checkpoints / ingestion_state / ingestion_runs** – Track resumable progress for long-running jobs.

## How to “start the machine” (run end-to-end ingestion)

Run these commands from the repo root, in order, after configuring the environment and database:

1. **Ball Don't Lie schedules + stats** – Populates teams, games, and box score tables.
   ```bash
   python -m nba_ingest.balldontlie_ingest --start 2021-10-19 --end 2025-11-11
   ```

2. **nba_api mapping + play-by-play** – Fills `games.nba_game_id` and writes play-by-play rows.
   ```bash
   python -m nba_ingest.nba_api_ingest_pbp --start 2021-10-19 --end 2025-11-11 --delay 1.0
   ```

3. **Team odds + player props (unified orchestrator)** – Runs BetsAPI, SGO, and The Odds API sequentially with resumable checkpoints.
   ```bash
   python -m nba_ingest.unified_odds_ingest --start 2021-10-19 --end 2025-11-11
   ```

   *Internally, the orchestrator processes BetsAPI team markets for the full window, SGO player props through 2023-05-02, then The Odds API props from 2023-05-03 forward. Progress is recorded in `ingestion_state` so reruns continue from the next incomplete date.*

4. **(Optional) Dedicated hybrid props worker** – If you need tighter control over props ingestion (e.g., separate scheduling), call the resumable worker directly.
   ```bash
   python -m nba_ingest.props_hybrid_ingest \
     --start 2021-10-19 \
     --end 2025-11-11 \
     --markets player_points,player_assists,player_rebounds,player_threes \
     --region us \
     --resume true
   ```

5. **(Optional) Standalone parquet exporter** – Writes nba_api play-by-play logs per game as Parquet for ad-hoc analysis.
   ```bash
   python nba_pbp_ingest.py
   ```

## Provider-specific notes

### SportsGameOdds (player props: 2021-10-19 → 2023-05-02)
* Uses `SPORTSGAMEODDS_API_KEY` and enforces 10 req/min plus the monthly object quota.
* `sports_game_odds_client.py` handles pagination, retries, and rate limiting.
* `sgo_ingest.py` / `props_hybrid_ingest.py` normalize team/player names, map events into `games`, and upsert props (`player_points`, `player_assists`, `player_rebounds`, `player_threes`) into `odds_markets` with `provider='sportsgameodds'`.

### The Odds API (player props: 2023-05-03 → 2025-11-11)
* `odds_api_client.py` enforces 30 req/min and retries 429/5xx responses with backoff.
* `odds_api_ingest.py` and the unified orchestrator call the historical endpoints at a canonical timestamp (default `12:00:00Z`). Empty responses (no props for a game) are treated as success so checkpointing never stalls.

### BetsAPI (team moneyline/spread/totals: 2021-10-19 → 2025-11-11)
* `unified_odds_ingest.py` fetches daily schedules, maps events to `games`, and writes moneyline, spread, and total markets with `participant_type='team'`.
* Rate limiting is conservative (1 request every ~0.75–1.0 seconds) to honor BetsAPI’s free-tier guidance.

## Testing & validation

Run the test suite to exercise rate limiting, normalization, and ETL idempotency with fixture payloads:
```bash
cd nba_ingest
pytest
```

For ad-hoc verification in Postgres:
```sql
-- Sample join showing stats, odds, and play-by-play on a single game
SELECT g.id, g.game_date, t_home.name AS home, t_away.name AS away,
       COUNT(pbp.id) AS pbp_events,
       COUNT(DISTINCT pg.player_id) AS players_with_stats,
       COUNT(DISTINCT gm.id) AS odds_rows
FROM games g
JOIN teams t_home ON t_home.id = g.home_team_id
JOIN teams t_away ON t_away.id = g.away_team_id
LEFT JOIN play_by_play pbp ON pbp.game_id = g.id
LEFT JOIN player_game_stats pg ON pg.game_id = g.id
LEFT JOIN odds_markets gm ON gm.event_id = g.id
WHERE g.game_date = DATE '2021-10-19'
GROUP BY 1,2,3,4;
```

## Logging & operations

* CLI commands print per-provider counts, HTTP 429/5xx retries, and current rate-limit pacing every few minutes (configurable via `--log-interval-seconds`).
* `checkpoints`, `ingestion_runs`, and `ingestion_state` persist discovery + processing status so you can safely resume after API outages or SGO monthly resets.
* Always pilot a short date window (one week) when onboarding a new provider or markets list. Providers frequently omit certain markets/books for select games; the loaders treat missing markets as informational warnings rather than failures.

With these components, you can continuously ingest NBA schedules, stats, play-by-play, and odds data into a single warehouse that powers downstream analytics and betting products.
