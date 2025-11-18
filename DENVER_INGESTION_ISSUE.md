# Denver Nuggets Database Ingestion Issue - AI Assistant Instructions

## Problem Summary

The Denver Nuggets (and 6 other teams) have **incorrect team data** in the `teams` table, preventing BalldontLie API from ingesting their games. This results in:
- **0 Denver games** in the database (should have ~82 per season × 4 seasons = ~320 games)
- **3,317 unmatched SDV play-by-play events** because games don't exist to match against
- Complete failure of the ETL pipeline for affected teams

## Root Cause

### Issue 1: Wrong Team Abbreviation
**Current value:** `abbreviation = "DN"`
**Required value:** `abbreviation = "DEN"`
**Impact:** SDV data uses "DEN" but database has "DN", preventing team resolution in `_resolve_team_id()`

### Issue 2: Wrong BalldontLie Team ID
**Current value:** `bdl_team_id = 50`
**Required value:** `bdl_team_id = 8`
**Impact:** BalldontLie API uses ID 8 for Denver, but `_ensure_game()` function in `balldontlie_ingest.py:154-288` looks up teams by `bdl_team_id`. When it can't find a team with `bdl_team_id=50`, it returns `None` and **skips creating the game entirely**.

### Critical Code Path (balldontlie_ingest.py)
```python
def _ensure_game(session: Session, payload: dict) -> int | None:
    home_team = payload.get("home_team") or {}
    away_team = payload.get("visitor_team") or {}
    home_id = _team_db_id(session, home_team.get("id"))  # ← Looks up by bdl_team_id
    away_id = _team_db_id(session, away_team.get("id"))
    if not home_id or not away_id:  # ← Denver fails here
        return None  # ← Game is NOT created, silently skipped
```

### Why Updates Haven't Persisted

Multiple `UPDATE` statements were provided but **didn't commit to the database**:
```sql
UPDATE teams SET abbreviation = 'DEN', bdl_team_id = 8 WHERE id = 8;
```

**Problem:** PostgreSQL requires explicit `COMMIT` to persist transaction changes. The user's database client likely didn't auto-commit, so changes were rolled back.

## Affected Teams

7 teams total have wrong abbreviations (verified from unmatched_2022.csv analysis):

| Team ID | Current Abbrev | Required Abbrev | Current bdl_team_id | Required bdl_team_id |
|---------|----------------|-----------------|---------------------|----------------------|
| 8       | DN             | DEN             | 50                  | 8                    |
| 10      | GSW            | GS              | (unknown)           | 10                   |
| 19      | NOP            | NO              | (unknown)           | 19                   |
| 20      | NYK            | NY              | (unknown)           | 20                   |
| 27      | SAS            | SA              | (unknown)           | 27                   |
| 29      | UTA            | UTAH            | (unknown)           | 29                   |
| 30      | WAS            | WSH             | (unknown)           | 30                   |

## What Has Been Tried

### 1. Enhanced Match-Map Logic (COMPLETED ✅)
**File:** `sdv_pbp_etl.py:226-381`
**Changes:** Added fallback strategies for matching (swapped teams, ±1 day offset, better diagnostics)
**Result:** Improved matching capability, but can't match games that don't exist

### 2. Added CSV Export to Diagnose Command (COMPLETED ✅)
**File:** `sdv_pbp_etl.py:388-432`
**Changes:** `diagnose` command now exports `unmatched_YYYY.csv`
**Result:** Successfully identified 3,317 unmatched games, all involving teams with wrong abbreviations

### 3. Fixed Column Naming Conflict (COMPLETED ✅)
**File:** `a54df16a4c3c_add_sdv_game_map_and_raw_game_id.py`
**Changes:** Use `internal_game_id` instead of `game_id` to avoid conflict with SDV's game_id
**Result:** Migration runs without errors, column properly mapped

### 4. Added SDV Parquet Filter Utility (COMPLETED ✅)
**File:** `sdv_pbp_filter.py`
**Changes:** Created `inspect` and `filter` commands to extract regular season games
**Result:** User successfully filtered 2022-2025 seasons and imported to raw tables

### 5. Added Per-Season Notifications (COMPLETED ✅)
**File:** `balldontlie_ingest.py:700-727`
**Changes:** Notifications when each season completes ingestion
**Result:** Better progress tracking during long ingestion runs

### 6. Attempted Team Data Updates (FAILED ❌)
**Action:** Provided multiple UPDATE statements to fix abbreviations and bdl_team_id
**Result:** Changes didn't persist, likely because COMMIT wasn't executed
**Evidence:** Query shows Denver still has `abbreviation="DN"` and `bdl_team_id=50`

### 7. Re-ran BalldontLie Ingestion (FAILED ❌)
**Action:** User cleared `ingestion_status` and re-ran ingestion
**Result:** Denver still has 0 games because team data is still wrong
**Evidence:** Game counts unchanged (1148 for 2022, should be ~1230 with Denver's 82 games)

## Current Database State

### Verification Query Results
```sql
SELECT id, abbreviation, name, bdl_team_id FROM teams WHERE id = 8;
```
**Output:** `8,"DN","Denver Nuggets",50`
**Status:** ❌ WRONG - Both values incorrect

### Game Count by Team (2022 season)
- Most teams: 78-80 games ✅
- Denver Nuggets: **0 games** ❌
- Total games: 1,148 (missing ~82 Denver games)

### SDV Raw Tables
- `sdv_nba_pbp_2022_raw`: Imported ✅
- `sdv_nba_pbp_2023_raw`: Imported ✅
- `sdv_nba_pbp_2024_raw`: Imported ✅
- `sdv_nba_pbp_2025_raw`: Imported ✅
- All tables have `internal_game_id` column (nullable) ✅

