# NBA Ingestion Project

This repository contains a Python project for ingesting NBA data from the [Ball Don't Lie API](https://api.balldontlie.io) into a PostgreSQL database. The project downloads team, game, and player advanced statistics for configurable NBA seasons. The stored data will be used in future phases to power an NBA win probability model and related betting tools.

## Project Structure

```
nba_ingest/
├── nba_ingest/
│   ├── __init__.py
│   ├── config.py
│   ├── db.py
│   ├── models.py
│   ├── balldontlie_client.py
│   ├── ingest.py
│   ├── features.py
│   ├── train_model.py
│   ├── win_prob_model.py
│   ├── odds_math.py
│   ├── odds_ingest.py
│   └── api.py
├── models/
│   └── win_prob_model.pkl  (created after training)
│   └── win_prob_model.py
├── models/
│   └── win_prob_model.pkl  (created after training)
│   └── ingest.py
├── requirements.txt
└── .env
```

## Getting Started

1. **Create and activate a virtual environment**

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

2. **Install dependencies**

   ```bash
   pip install -r nba_ingest/requirements.txt
   ```

3. **Configure environment variables**

   Create a `.env` file (or update the provided placeholder) with the following entries:

   ```env
   BALDONTLIE_API_KEY=your_key_here
   DATABASE_URL=postgres://user:password@host:port/dbname
   NBA_SEASONS=2021,2022,2023,2024
   SPORTSGAMEODDS_API_KEY=your_sgo_key
   ODDS_API_KEY=your_the_odds_api_key
   BETSAPI_API_KEY=your_betsapi_key
   ```

   * `BALDONTLIE_API_KEY` – Required API key for the Ball Don't Lie API.
   * `DATABASE_URL` – PostgreSQL connection string.
   * `NBA_SEASONS` – Optional comma-separated list of seasons to ingest. If omitted, the last four seasons are used.
   * `SPORTSGAMEODDS_API_KEY` – Credentials for the SportsGameOdds v2 API (player props through 2023-05-02).
   * `ODDS_API_KEY` – Credentials for The Odds API historical NBA endpoint (props from 2023-05-03 onward).
   * `BETSAPI_API_KEY` – Credentials for BetsAPI’s NBA feed (team moneyline/spread/total markets across the full window).

4. **Run the ingest script**

   ```bash
   python -m nba_ingest.ingest
   ```

## Database Tables

All providers now share a unified warehouse schema so that every row references the same `teams`, `players`, and `games` records. The core tables are:

- **`teams`** – Canonical metadata for each NBA franchise with `bdl_team_id`, `nba_team_id`, and a `canonical_name` used when matching provider payloads.
- **`players`** – Canonical player identities with Ball Don’t Lie/NBA IDs, normalized names, and basic bio fields plus timestamps.
- **`games`** – One row per real game with references to home/away teams, season/season_type, final scores, tip-off time, and provider IDs (`bdl_game_id`, `nba_game_id`, `odds_api_event_id`, `sgo_event_id`).
- **`player_game_stats`** – Traditional box score stats from Ball Don’t Lie keyed by `(game_id, player_id)`.
- **`player_game_advanced`** – Advanced metrics from Ball Don’t Lie keyed by `(game_id, player_id)`.
- **`play_by_play`** – Full play-by-play events from `nba_api` tied back to the canonical game and players.
- **`game_odds`** – Unified table for team markets and player props across The Odds API and SportsGameOdds. Rows capture provider, bookmaker, market/line metadata, American odds, inferred participant (team or player), last update timestamps, and the ingestion snapshot.

Alembic migrations manage these tables. Run `alembic upgrade head` after pulling new changes to ensure your schema is current.

## Phase 2 – Win Probability Model

With the ingestion pipeline in place, the project now includes utilities for training and using a pre-game home-team win probability model.

1. **Ensure data is ingested**

   Run the ingestion script (see above) so the latest games and advanced stats are available in the database.

2. **Train the model**

   ```bash
   python -m nba_ingest.train_model
   ```

   The script pulls historical features from the database, trains a logistic regression model, prints evaluation metrics (accuracy, ROC AUC, Brier score), and saves the fitted pipeline to `models/win_prob_model.pkl`.

3. **Generate a prediction for a matchup**

   ```bash
   python -m nba_ingest.win_prob_model --home_team_id=14 --visitor_team_id=20 --date=2024-12-15
   ```

   This command loads the saved model, assembles features for the specified matchup/date, and prints the predicted probability that the home team wins.

The model currently focuses on pre-game home win probability using season-long trends, recent team form, and rest days derived from the ingested advanced statistics. Later phases will integrate betting odds and power a public-facing API.

## Unified ETL modules

Each provider has a dedicated module that reads from its API, normalizes the payload, and upserts into the canonical tables:

1. **Ball Don't Lie (teams, games, stats)**

   ```bash
   python -m nba_ingest.balldontlie_ingest
   ```

   The script syncs `teams`, `games`, `player_game_stats`, and `player_game_advanced`. It matches by Ball Don’t Lie IDs first, then falls back to canonical names, ensuring re-runs are idempotent.

2. **NBA API scoreboard + play-by-play**

   ```bash
   python -m nba_ingest.nba_api_ingest_pbp --start 2021-10-19 --end 2025-11-11 --delay 1.0
   ```

   For each date in the window the script maps `nba_game_id` onto existing `games` rows via home/away teams, then fetches play-by-play logs. Every event is keyed by `(game_id, event_num)` so rerunning the command safely refreshes prior data.

3. **The Odds API team odds + player props**

   ```bash
   python -m nba_ingest.odds_api_ingest --start 2023-05-03 --end 2023-06-30 --markets player_points,player_assists
   ```

   Historical snapshots are pulled at a configurable timestamp (defaults to `12:00:00Z`). Team names are normalized to match the `games` table, provider event IDs are persisted, and every bookmaker/market/outcome is written to `game_odds` with proper participant foreign keys.

4. **SportsGameOdds player props**

   ```bash
   python -m nba_ingest.sgo_ingest --start 2021-10-19 --end 2023-05-02 --markets player_points,player_rebounds
   ```

   Events are mapped by date + teams, the `games.sgo_event_id` is filled, and all props are saved into `game_odds` with `provider='sgo'`. Set `SGO_INCLUDE_ALT_LINES=true` if you want to record alternate offerings.

All commands expect `DATABASE_URL` plus the provider-specific API keys described below to be present in your environment.

## Phase 3 – API and Odds

Phase 3 extends the project with betting odds ingestion, pricing math helpers, and a FastAPI backend that surfaces the win probability model alongside market information.

1. **Install updated dependencies**

   Additional libraries (`fastapi`, `uvicorn[standard]`, `nba_api`) have been added to `nba_ingest/requirements.txt`. Re-run the installation step if you installed dependencies before this phase.

2. **(Optional) Cache daily odds data**

   The new odds ETL flows (`nba_ingest.odds_api_ingest` and `nba_ingest.sgo_ingest`) supersede the original `odds_ingest` script by writing into the canonical `game_odds` table. You can still call the legacy script if you need backward compatibility, but new data should flow through the unified ingest described above.

3. **Run the API**

   ```bash
   uvicorn nba_ingest.api:app --reload --host 0.0.0.0 --port 8000
   ```

   The API expects `BALDONTLIE_API_KEY`, `DATABASE_URL`, and `NBA_SEASONS` (for model feature generation) to be present in your environment or `.env` file. It lazily loads the trained model from `models/win_prob_model.pkl`; if the file is missing the win probability fields will be `null` until the model is trained.

4. **Available endpoints**

   - `GET /health` – Simple status probe returning `{ "status": "ok" }`.
   - `GET /games?date=YYYY-MM-DD` – Returns the day’s games with team info, home win probability from the model, all cached sportsbook markets from `game_odds`, and the best available moneyline price for each side. If cached odds are stale or missing, the API will fetch fresh odds and update the database before responding.
   - `POST /parlay/estimate` – Accepts a list of parlay legs (with American odds), computes combined hit probability, fair odds, and expected value when offered odds are supplied.

   Example request for a parlay estimate:

   ```bash
   curl -X POST \
     -H "Content-Type: application/json" \
     -d '{
           "legs": [
             {"game_id": 1234, "line_type": "moneyline", "side": "home", "american_odds": -125},
             {"game_id": 5678, "line_type": "moneyline", "side": "away", "american_odds": +140}
           ],
           "stake": 10,
           "parlay_offered_american_odds": +350
         }' \
     http://localhost:8000/parlay/estimate
   ```

These additions prepare the project for integrating sportsbook lines with the statistical model and lay the groundwork for future betting tools.

## Unified odds and props ingestion

Sports betting data from three providers now lands in one cohesive schema driven by the `leagues`, `teams`, `players`, `games`, `odds_books`, `odds_markets`, and `ingestion_state` tables.

* `leagues` tracks high-level metadata (currently just the NBA) and the provider sport keys used when calling each API.
* `teams`/`players` store canonical entities with JSON mappings of provider IDs (`provider_team_ids`, `provider_player_ids`).
* `games` (events) carry the league, season info, start time, and `provider_event_ids` JSON so every provider references the exact same row.
* `odds_books` deduplicates bookmaker metadata while `odds_markets` stores every snapshot keyed by `(event_id, provider, bookmaker, market_type, participant_type, participant_id, side, line, as_of)`.
* `ingestion_state` records the last successful date per provider so each worker can resume after failures or quota resets.

### Provider coverage

| Provider          | Scope                                | Window                     | Rate limits |
|-------------------|--------------------------------------|----------------------------|-------------|
| SportsGameOdds    | Player props (points/assists/rebounds/threes) | 2021-10-19 → 2023-05-02    | 10 req/min + 2,500 events/month |
| The Odds API      | Player props (points/assists/rebounds/threes) | 2023-05-03 → 2025-11-11    | 30 req/min |
| BetsAPI           | Team moneyline, spreads, totals       | 2021-10-19 → 2025-11-11    | Provider throttles (~1–2 req/sec) |

Each loader enforces the documented throttle before every HTTP request so we respect free-plan quotas. When a provider returns partial data (missing books or certain markets) the ingest logs a warning but still marks the day complete so the pipeline remains resumable.

### Running the end-to-end loader

```
python -m nba_ingest.unified_odds_ingest --start 2021-10-19 --end 2025-11-11
```

The orchestrator will:

1. Ensure the schema exists (via SQLAlchemy metadata) and create the `NBA` league row if needed.
2. Run BetsAPI for the requested window first (moneyline/spread/total markets).
3. Backfill SportsGameOdds props for dates up to 2023-05-02.
4. Finish with The Odds API historical props for dates on/after 2023-05-03.

Each provider updates its `ingestion_state` row after completing a date so reruns automatically pick up from the next day. Logs include the processed date, number of discovered events, and number of odds rows written to help operators verify coverage. CSV exports or sample validation queries can be added once live API keys are configured, but the tests exercise each loader with fixture responses to guarantee the schema and upserts behave as expected.
## Hybrid Player Props Ingestion

The repository now ships with an end-to-end pipeline that backfills and maintains historical NBA player props by combining two providers:

* **SportsGameOdds (SGO)** – Covers 2021-10-19 through 2023-05-02. We run a single free-plan account with a hard quota of 2,500 events per calendar month and a rate limit of **10 requests/minute**. Each fetched event counts toward the monthly object quota, so the worker keeps a 50-event buffer and pauses automatically once the quota is nearly exhausted.
* **The Odds API** – Supplies historical props from 2023-05-03 forward. The historical endpoint enforces **30 requests/minute**, which the worker throttles before every request (discovery and market pulls). The Odds API’s dataset begins on 2023-05-03T05:30:00Z; earlier dates must be handled by SportsGameOdds or skipped.

Because the providers expose different windows, every backfill splits on **2023-05-03** (exclusive lower bound for The Odds API). The CLI automatically routes each date to the correct provider and is safe to resume in-place thanks to checkpoint rows and database upserts.

### Database schema and migrations

Player props live in their own tables (`players`, `events`, `props`, `checkpoints`, `ingestion_runs`). Apply the Alembic migration before running the CLI:

```bash
alembic upgrade head
```

The CLI still attempts to create tables opportunistically, but running the migration keeps schema drift under control for future releases.

### Environment configuration

Add the following keys to `.env` (or the process environment). All are required unless a default is noted.

```env
DATABASE_URL=postgres://user:password@host:5432/dbname
SGO_API_KEY=...
SGO_BASE_URL=https://api.sportsgameodds.com
SGO_OBJECTS_PER_MONTH=2500
SGO_REQS_PER_MIN=10
SGO_INCLUDE_ALT_LINES=false
ODDS_API_KEY=...
ODDS_API_BASE_URL=https://api.the-odds-api.com/v4
ODDS_REQS_PER_MIN=30
PROP_MARKETS=player_points,player_assists,player_rebounds,player_threes
REGION=us
HISTORICAL_START=2021-10-19
HISTORICAL_SPLIT=2023-05-03
HISTORICAL_END=2025-11-11
DRY_RUN=false
GLOBAL_MAX_EVENTS=7000
TOS_ACK_SINGLE_ACCOUNT=true
```

`PROP_MARKETS` is parsed as CSV, while booleans accept `true/false/1/0`. The SGO worker refuses to run unless `TOS_ACK_SINGLE_ACCOUNT=true` to acknowledge that we are intentionally using a single free-tier account per the provider’s ToS.

### CLI usage

Run the hybrid ingest from the repository root:

```bash
python -m nba_ingest.props_hybrid_ingest \
  --start 2021-10-19 \
  --end 2025-11-11 \
  --markets player_points,player_assists,player_rebounds,player_threes \
  --region us \
  --resume true
```

Important behavior:

* **Discovery + checkpoints** – Every event is discovered once per provider/date and stored in the `checkpoints` table. Re-running with `--resume true` keeps previously-discovered events and only adds new ones, making the process idempotent.
* **Rate limiting** – The coordinator enforces the per-provider limits (10 req/min for SGO, 30 req/min for The Odds API) before every HTTP call. 429 responses trigger a 60-second wait with up to five retries; 5xx responses use exponential backoff capped at 64 seconds.
* **Partial market support** – If a provider returns zero props for a game, the run logs a warning but still marks the checkpoint as done so it will not get stuck.
* **Logging + reporting** – Every five minutes (configurable via `--log-interval-seconds`) the CLI prints global progress, provider throughput, rate-limit/5xx counters, current Odds API request rate, and the SGO quota estimate. When the run finishes it writes `reports/props_hybrid_ingest_<timestamp>.csv` that captures each checkpoint, outcome, and elapsed processing time.

### Ops tips

* **Pilot before the full backfill** – Run a small window (for example, one week) before attempting the entire historical range to verify market coverage and bookmaker availability. Not every market will be present for every game; this variability is normal and the pipeline simply stores whatever is available.
* **Monthly SGO quota resets** – When the `SGO_OBJECTS_PER_MONTH` quota is nearly exhausted the SGO worker stops claiming new checkpoints automatically. Resume the run after the provider resets usage by re-running the same CLI command with `--resume true`; the pending SGO checkpoints remain queued in the database.
* **Dry runs** – Set `DRY_RUN=true` (or pass `--dry-run true`) to validate discovery, checkpointing, and logging without making provider API calls.
* **Safety caps** – `GLOBAL_MAX_EVENTS` limits how many checkpoints the workers claim in a single invocation to guard against runaway costs. If the cap triggers the CLI prints a message explaining how to resume.

When deploying this ingestion process in production, ensure that Alembic migrations run before each release, that environment variables remain secret, and that you track monthly SGO usage so you can plan resumptions around the free-plan quota reset.
