# System Architecture – NBA Win Probability Project

This document describes the end-to-end architecture of the NBA win probability system: how data flows from external APIs into Postgres, how features are generated, and how the prediction model is trained and used.

---

## 1. High-Level Overview

At a high level, the system is:

1. **Data ingestion layer**  
   - Pulls game, team, player, and odds data from multiple APIs.
   - Normalizes and stores everything in a Postgres warehouse.

2. **Feature engineering layer**  
   - Builds game-level features (one row per game) that capture team strength, recent performance, rest, and (optionally) betting markets.

3. **Modeling layer**  
   - Trains a supervised model to predict the probability that the home team wins.
   - Provides a reusable inference function to score new games.

4. **Orchestration / scripts**  
   - CLI entry points to backfill historical data and keep data up to date.
   - Designed to eventually plug into a scheduled job or cron.

---

## 2. Data Sources

The project uses three main families of external APIs:

### 2.1 BallDontLie (balldontlie.io)

Used for:

- Teams (names, abbreviations, conferences, divisions)
- Games (dates, home/away teams, scores)
- Player box scores and advanced stats

Key script:

- `nba_ingest/balldontlie_ingest.py`  
  - Ingests:
    - Teams
    - Games for one or more seasons
    - Player box scores
    - Player advanced stats

### 2.2 NBA API (nba_api package)

Used for:

- Scoreboard data with official NBA `GAME_ID`s
- Play-by-play event streams

Key script:

- `nba_ingest/nba_api_ingest_pbp.py`  
  - Maps internal `games` rows to NBA `GAME_ID`s.
  - Ingests detailed play-by-play events into Postgres.
  - Respects API rate limits and uses a configurable delay.

### 2.3 Betting Odds Providers

Used for:

- Pre-game **team odds** (moneyline, spreads, totals)
- (Later) Player props (points, assists, rebounds, threes, etc.)

Providers:

- **The Odds API**
- **SportsGameOdds (SGO)**
- **BetsAPI**

Key script:

- `nba_ingest/unified_odds_ingest.py`  
  - Unified ingestion pipeline that:
    - Pulls odds from The Odds API, SGO, and BetsAPI.
    - Normalizes data into the shared `game_odds` table.
    - Supports configurable date ranges and markets.

There are also legacy/specialized scripts:

- `odds_ingest.py` – older odds ingest (e.g., from BallDontLie).
- `odds_api_ingest.py` – direct ingest of The Odds API.
- `sgo_ingest.py` – direct ingest of SGO.

For regular use, `unified_odds_ingest.py` is the primary, consolidated entry point.

---

## 3. Database Schema (Postgres)

The warehouse is a Postgres database accessed via SQLAlchemy. The core logical tables include:

- `teams`
  - One row per NBA team.
  - Fields like:
    - `id`
    - `name`, `abbreviation`, `city`
    - `conference`, `division`
    - `canonical_name`
    - Provider-specific IDs (e.g., `bdl_team_id`, `nba_team_id`)

- `games`
  - One row per NBA game.
  - Fields like:
    - `id`
    - `season`
    - `date` / `tipoff_datetime_utc`
    - `home_team_id`, `away_team_id`
    - `home_score`, `away_score`
    - External IDs (`bdl_game_id`, `nba_game_id`)
    - Flags (e.g., `postseason`)

- `player_stats` / `player_advanced_stats`
  - Per-game player-level stats from BallDontLie:
    - Minutes, points, rebounds, assists, etc.
    - Advanced metrics as available.

- `play_by_play_events`
  - One row per play-by-play event from NBA API:
    - `game_id`
    - Period, clock, event type
    - Teams, players, scores after the event
    - Raw event text where available

- `game_odds`
  - Unified table for odds from Odds API, SGO, and BetsAPI.
  - Fields like:
    - `id`
    - `game_id` (FK to `games`)
    - `provider`, `bookmaker_key`, `market_key`
    - `participant_type` (`team` or `player`)
    - `participant_team_id` / `participant_name`
    - `side` (home/away/over/under)
    - `line` (spread value, total points, etc.)
    - `price` (American odds)
    - `snapshot_ts` (timestamp of the odds snapshot)

This schema is designed so that:

- `games` is the central hub.
- All modeling can be done by joining `games` to `teams`, `player_stats`, `play_by_play_events`, and `game_odds`.

---

## 4. Ingestion Layer

### 4.1 BallDontLie Ingest – `balldontlie_ingest.py`

Responsibilities:

- Download and upsert:
  - Teams
  - Games
  - Player box score stats
  - Advanced stats
- Supports:
  - Season-based ingest (via config).
  - Date-based ingest via `--start` and `--end` that are converted into seasons.

Implementation details:

- Uses a `BallDontLieClient` wrapper around the HTTP API.
- Uses SQLAlchemy’s `insert(...).on_conflict_do_update` to upsert teams and games.
- Ensures team uniqueness via `canonical_name` so different providers map into the same `teams` row.

### 4.2 Unified Odds Ingest – `unified_odds_ingest.py`

Responsibilities:

- Iterate over a date range and fetch odds from:
  - BetsAPI
  - The Odds API
  - SportsGameOdds
- Normalize and write into `game_odds`.

Key ideas:

- Uses provider-specific clients to fetch raw JSON.
- Maps provider event IDs and team names back to internal `games` and `teams` using:
  - Canonical team names
  - Date/time proximity
- Writes all odds into the same `game_odds` schema, so downstream code can treat odds uniformly regardless of provider.

### 4.3 NBA Play-by-Play Ingest – `nba_api_ingest_pbp.py`

Responsibilities:

1. For each date in a range:
   - Use `ScoreboardV2` to find NBA `GAME_ID`s for that day.
   - Link those NBA game IDs to existing `games` rows.

2. For each mapped game:
   - Call `PlayByPlayV2` to fetch all events.
   - Normalize and insert events into `play_by_play_events`.

Key considerations:

- Uses a delay between API calls to respect NBA API rate limits.
- Can be re-run safely: events are keyed in a way that avoids duplicate inserts.

---

## 5. Feature Engineering Layer

The feature engineering logic lives primarily in `features.py`.

### 5.1 Game-Level Training Data

The core function (conceptually):

- `get_training_dataframe(engine, seasons, rolling_window)`

Responsibilities:

- Query `games`, `teams`, and stats tables for the given seasons.
- Compute team strength metrics, such as:
  - Season-average net rating (offensive - defensive rating).
  - Season-average offensive and defensive ratings.
  - Season pace.
- Compute recent-form metrics:
  - Rolling averages over the last `N` games (e.g., last 10 games):
    - Net rating
    - Point differential
- Compute rest features:
  - `home_rest_days`
  - `away_rest_days`
  - Potential to extend to back-to-backs, long road trips, etc.
- Combine into **difference features**:
  - `diff_season_net_rating` (home – away)
  - `diff_recent_net_rating`
  - `diff_season_off_rating`, `diff_season_def_rating`
- Generate target label:
  - `home_win` = 1 if home_score > away_score, else 0.

The output is a pandas DataFrame with:

- One row per historical game.
- A set of numeric feature columns.
- The binary target `home_win`.

Later, this DataFrame can be extended to include:

- Implied probabilities from odds markets.
- Aggregated player props.
- Injury and rotation information.

---

## 6. Modeling Layer

The modeling logic is split into:

- `train_model.py`
- `win_prob_model.py`

### 6.1 Training – `train_model.py`

Responsibilities:

1. Load training data from `features.get_training_dataframe(...)`.
2. Split data into training and holdout sets by season:
   - Example: train on older seasons, test on more recent ones.
3. Build a scikit-learn pipeline, e.g.:
   - `StandardScaler` → `LogisticRegression`
4. Train the model to predict `home_win`.
5. Compute evaluation metrics:
   - Accuracy
   - ROC AUC
   - Calibration-oriented metrics (e.g., Brier score) in the future.
6. Serialize the trained model:
   - Save to `models/win_prob_model.pkl`.

### 6.2 Inference – `win_prob_model.py`

Responsibilities:

- Load the persisted model.
- For a given matchup and date:
  - Recompute the same features used in training (season stats, recent form, rest, and odds once integrated).
  - Call `model.predict_proba(...)` to output `P(home_win)`.
- Provide a simple interface to be:
  - Imported from other Python modules, or
  - Called via a CLI interface (e.g., “predict today’s games”).

This abstraction allows:

- A CLI script to generate nightly predictions.
- Future web/API frontends to reuse the same model without duplicating logic.

---

## 7. Configuration & Secrets

Configuration is centralized via a settings loader (e.g., `load_settings()`), which reads:

- `DATABASE_URL` (Postgres)
- API keys:
  - BallDontLie
  - The Odds API
  - SportsGameOdds
  - BetsAPI
- `NBA_SEASONS` list for backfill.
- Rate-limiting and delay settings for API clients.

This keeps secrets out of the code and makes it easy to run the same code in different environments.

---

## 8. End-to-End Flow

**Historical backfill:**

1. Run `balldontlie_ingest.py` over seasons or date range.
2. Run `nba_api_ingest_pbp.py` over the same or larger date range.
3. Run `unified_odds_ingest.py` over the same date range to populate `game_odds`.
4. Run `train_model.py` to train and save a model.

**Daily operation (future state):**

1. Ingest yesterday’s games and today’s updated odds.
2. Build a feature table for today’s upcoming games.
3. Run `win_prob_model.py` to generate `P(home_win)` for each game.
4. (Optional) Compute edges vs implied odds and surface recommended bets.
