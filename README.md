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
   ```

   * `BALDONTLIE_API_KEY` – Required API key for the Ball Don't Lie API.
   * `DATABASE_URL` – PostgreSQL connection string.
   * `NBA_SEASONS` – Optional comma-separated list of seasons to ingest. If omitted, the last four seasons are used.

4. **Run the ingest script**

   ```bash
   python -m nba_ingest.ingest
   ```

## Database Tables

The ingestion process populates the following tables:

- **`nba_teams`** – Stores team metadata (name, city, conference, division).
- **`nba_games`** – Contains detailed per-game information, including quarter scoring, timeouts, and bonus indicators for each team.
- **`nba_player_advanced_stats`** – Holds player-level advanced metrics for every game, such as PIE, offensive/defensive ratings, usage percentage, and more.
- **`nba_game_odds`** – Stores sportsbook moneyline, spread, and total prices for each game, keyed by vendor and market type.

These tables form the foundation for future analytics, including win probability modeling and betting insights.

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

## Phase 3 – API and Odds

Phase 3 extends the project with betting odds ingestion, pricing math helpers, and a FastAPI backend that surfaces the win probability model alongside market information.

1. **Install updated dependencies**

   Additional libraries (`fastapi`, `uvicorn[standard]`) have been added to `nba_ingest/requirements.txt`. Re-run the installation step if you installed dependencies before this phase.

2. **(Optional) Cache daily odds data**

   ```bash
   python -m nba_ingest.odds_ingest --date=2024-12-25
   ```

   The command fetches odds for the supplied date (defaults to today), normalizes sportsbook payloads, and upserts rows into `nba_game_odds`.

3. **Run the API**

   ```bash
   uvicorn nba_ingest.api:app --reload --host 0.0.0.0 --port 8000
   ```

   The API expects `BALDONTLIE_API_KEY`, `DATABASE_URL`, and `NBA_SEASONS` (for model feature generation) to be present in your environment or `.env` file. It lazily loads the trained model from `models/win_prob_model.pkl`; if the file is missing the win probability fields will be `null` until the model is trained.

4. **Available endpoints**

   - `GET /health` – Simple status probe returning `{ "status": "ok" }`.
   - `GET /games?date=YYYY-MM-DD` – Returns the day’s games with team info, home win probability from the model, all cached sportsbook markets, and the best available moneyline price for each side. If cached odds are stale or missing, the API will fetch fresh odds from Ball Don't Lie and update the database before responding.
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
