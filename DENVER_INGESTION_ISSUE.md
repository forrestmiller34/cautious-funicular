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

## Required Fix (STEP-BY-STEP)

### Step 1: Fix Team Data with Explicit COMMIT

**CRITICAL:** Must run these statements **WITH COMMIT** or use auto-commit mode:

```sql
BEGIN;

-- Fix Denver (highest priority)
UPDATE teams SET abbreviation = 'DEN', bdl_team_id = 8 WHERE id = 8;

-- Fix other affected teams
UPDATE teams SET abbreviation = 'GS' WHERE id = 10;
UPDATE teams SET abbreviation = 'NO' WHERE id = 19;
UPDATE teams SET abbreviation = 'NY' WHERE id = 20;
UPDATE teams SET abbreviation = 'SA' WHERE id = 27;
UPDATE teams SET abbreviation = 'UTAH' WHERE id = 29;
UPDATE teams SET abbreviation = 'WSH' WHERE id = 30;

COMMIT;
```

**Verification:**
```sql
SELECT id, abbreviation, name, bdl_team_id
FROM teams
WHERE id IN (8, 10, 19, 20, 27, 29, 30)
ORDER BY id;
```

**Expected output for Denver:** `8,"DEN","Denver Nuggets",8`

### Step 2: Clear Ingestion Status for Re-Run

Only need to clear seasons with affected teams:

```sql
BEGIN;

DELETE FROM ingestion_status
WHERE source = 'balldontlie'
  AND season IN (2021, 2022, 2023, 2024, 2025);

COMMIT;
```

### Step 3: Re-Run BalldontLie Ingestion

This will fetch all missing games for Denver and other affected teams:

```powershell
python -m nba_ingest.balldontlie_ingest --start 2021-10-19 --end 2025-04-13
```

**Expected duration:** 20-30 minutes (with notifications every season)

**Verification:**
```sql
-- Should now show ~82 games per season for Denver
SELECT season, COUNT(*) as denver_games
FROM games
WHERE home_team_id = 8 OR visitor_team_id = 8
GROUP BY season
ORDER BY season;

-- Total game count should increase by ~650 games (82 games × 4 seasons × 2 teams on court)
SELECT season, COUNT(*) as total_games
FROM games
GROUP BY season
ORDER BY season;
```

### Step 4: Run Match-Map for All Seasons

After games exist, match SDV play-by-play events to them:

```powershell
python -m nba_ingest.sdv_pbp_etl --source-table sdv_nba_pbp_2022_raw --season 2022 match-map
python -m nba_ingest.sdv_pbp_etl --source-table sdv_nba_pbp_2023_raw --season 2023 match-map
python -m nba_ingest.sdv_pbp_etl --source-table sdv_nba_pbp_2024_raw --season 2024 match-map
python -m nba_ingest.sdv_pbp_etl --source-table sdv_nba_pbp_2025_raw --season 2025 match-map
```

**Expected result:** Match rate should improve from ~0% to 95%+

**Verification:**
```sql
SELECT matched, COUNT(*)
FROM sdv_game_map
GROUP BY matched;
```

### Step 5: Diagnose Any Remaining Unmatched Games

```powershell
python -m nba_ingest.sdv_pbp_etl --source-table sdv_nba_pbp_2022_raw --season 2022 diagnose
```

**Expected:** Only playoff games or special events should remain unmatched (user filtered to regular season only)

### Step 6: Backfill internal_game_id

Once games are matched, populate the `internal_game_id` column in raw tables:

```powershell
python -m nba_ingest.sdv_pbp_etl --source-table sdv_nba_pbp_2022_raw --season 2022 backfill-game-ids
python -m nba_ingest.sdv_pbp_etl --source-table sdv_nba_pbp_2023_raw --season 2023 backfill-game-ids
python -m nba_ingest.sdv_pbp_etl --source-table sdv_nba_pbp_2024_raw --season 2024 backfill-game-ids
python -m nba_ingest.sdv_pbp_etl --source-table sdv_nba_pbp_2025_raw --season 2025 backfill-game-ids
```

### Step 7: Run ETL to Final Tables

Transform and load play-by-play events into final `play_by_play_events` table:

```powershell
python -m nba_ingest.sdv_pbp_etl --source-table sdv_nba_pbp_2022_raw --season 2022 etl
python -m nba_ingest.sdv_pbp_etl --source-table sdv_nba_pbp_2023_raw --season 2023 etl
python -m nba_ingest.sdv_pbp_etl --source-table sdv_nba_pbp_2024_raw --season 2024 etl
python -m nba_ingest.sdv_pbp_etl --source-table sdv_nba_pbp_2025_raw --season 2025 etl
```

## Key Files and Functions

### balldontlie_ingest.py
- **Line 154-288:** `_ensure_game()` - Creates game records, fails if team lookup fails
- **Line 95-105:** `_team_db_id()` - Looks up team by `bdl_team_id`, returns None if not found
- **Line 700-727:** Per-season notifications

### sdv_pbp_etl.py
- **Line 215-225:** `_resolve_team_id()` - Resolves SDV team codes to database team IDs
- **Line 226-381:** `match_game_map()` - Matches SDV games to database games with fallbacks
- **Line 388-432:** `diagnose_unmatched()` - Exports unmatched games to CSV

### sdv_pbp_filter.py
- **Line 110-148:** `inspect_parquet_dates()` - Shows date ranges in parquet files
- **Line 12-69:** `filter_parquet_by_date()` - Filters parquet to date range

## Critical Success Factors

1. **COMMIT MUST BE EXECUTED** - PostgreSQL requires explicit commit
2. **Verify team data changed** - Query teams table after update before proceeding
3. **Re-run BalldontLie ingestion** - Games don't exist until this runs successfully
4. **Match-map only works after games exist** - Can't match to non-existent games
5. **Follow steps in order** - Each step depends on previous step completing

## Testing the Fix

After Step 3 (BalldontLie ingestion), run this verification:

```sql
-- Should show ~82 games for Denver in each season
SELECT
    g.season,
    COUNT(*) as denver_games,
    MIN(g.game_date) as first_game,
    MAX(g.game_date) as last_game
FROM games g
WHERE g.home_team_id = 8 OR g.visitor_team_id = 8
GROUP BY g.season
ORDER BY g.season;
```

Expected output:
```
season | denver_games | first_game | last_game
-------|-------------|------------|----------
2022   | 82          | 2021-10-20 | 2022-04-10
2023   | 82          | 2022-10-19 | 2023-04-09
2024   | 82          | 2023-10-24 | 2024-04-14
2025   | ~40         | 2024-10-24 | (current)
```

## User's AI Assistant: What to Do

1. **Confirm the problem is understood:** Denver has wrong `abbreviation` and `bdl_team_id`
2. **Execute Step 1 with COMMIT:** Update team data and verify it persists
3. **Only proceed to Step 2-3 if Step 1 verification succeeds**
4. **Monitor ingestion progress:** Should see notifications every season
5. **Verify game counts increase:** Denver should have ~82 games per season
6. **If Step 3 succeeds, proceed with Steps 4-7**
7. **Report any errors immediately:** Include exact error message and context

## Common Pitfalls to Avoid

- ❌ Running match-map before games exist (will find 0 matches)
- ❌ Forgetting COMMIT after UPDATE statements
- ❌ Not verifying team data changed before re-running ingestion
- ❌ Skipping ingestion and trying to manually create games
- ❌ Running steps out of order

## Success Criteria

✅ Denver team has `abbreviation="DEN"` and `bdl_team_id=8`
✅ Denver has ~82 games per season in `games` table
✅ 95%+ of SDV games matched in `sdv_game_map`
✅ `internal_game_id` populated in raw tables
✅ Play-by-play events loaded into final `play_by_play_events` table

---

**Last Updated:** 2025-11-18
**Issue Status:** BLOCKED on Step 1 (team data update not persisting)
**Next Action:** Execute UPDATE statements WITH COMMIT
