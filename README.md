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

These tables form the foundation for future analytics, including win probability modeling and betting insights.
