# Model Improvement Roadmap

This roadmap focuses on improving the quality, robustness, and usefulness of the NBA win probability model. It is organized by phases so work can be done incrementally.

---

## Phase 1 – Strengthen the Baseline (Short Term)

**Goal:** Make the current logistic regression baseline as strong and trustworthy as possible.

1. **Integrate closing team odds as features**
   - From `game_odds`, derive:
     - `home_ml_closing`, `away_ml_closing` (American odds)
     - `home_implied_prob_closing`, `away_implied_prob_closing`
     - `home_spread_closing`, `total_points_closing`
   - Create derived features:
     - `diff_implied_prob = home_implied_prob_closing - away_implied_prob_closing`
     - `spread_abs = abs(home_spread_closing)`
   - Retrain the model and measure how much these features improve performance.

2. **Improve train/test splits**
   - Use **chronological splits by season** (e.g., train on 2021–22 & 2022–23, test on 2023–24).
   - Add a small **validation** segment inside the training period to tune hyperparameters.
   - Avoid random splits that leak future information into the training set.

3. **Add better evaluation metrics**
   - In addition to accuracy:
     - **Brier score** (probability calibration).
     - **ROC AUC**.
     - Calibration curves (predicted vs actual win rates in bins).
   - This makes it easier to compare future models.

4. **Sanity checks on features**
   - Verify that:
     - Higher `diff_season_net_rating` generally correlates with higher `P(home_win)`.
     - Favorites by spread have higher predicted probabilities on average.
   - Catch sign errors or bad joins early.

---

## Phase 2 – Richer Features (Medium Term)

**Goal:** Capture more of the real-world context that affects game outcomes.

1. **Recent form and schedule context**
   - Extend or refine existing metrics:
     - Last 5 / 10 games net rating (try different windows).
     - Separate home/away recent performance.
   - Add schedule strain:
     - Back-to-back flags.
     - “3 in 4” / “4 in 6” style fatigue indicators.
     - Length of current road trip.

2. **Player props aggregation (from SGO / Odds API)**
   - For each game, aggregate key player props:
     - `sum_home_points_line`, `sum_away_points_line`
     - `max_home_points_line`, `max_away_points_line`
     - Same for assists, rebounds, threes.
   - These act as a proxy for:
     - Star power.
     - Expected offensive usage.
     - Injury/rotation information implied by the market.

3. **Injury and availability signals**
   - Initially, infer from props:
     - Missing props for a star player can signal likely injury/rest.
   - Later, integrate explicit injury feeds if desired.

4. **Advanced team-level metrics**
   - From play-by-play + box scores, compute:
     - Clutch net rating (last 5 minutes of close games).
     - Transition vs half-court efficiency.
     - Fouling tendencies and free-throw rates.
   - Aggregate into season and recent windows, then use them as additional features.

---

## Phase 3 – Better Models & Training Procedure

**Goal:** Move beyond simple logistic regression while maintaining interpretability and stability.

1. **Tree-based models**
   - Experiment with:
     - Gradient Boosting (e.g., `GradientBoostingClassifier`).
     - XGBoost / LightGBM / CatBoost (if you’re comfortable adding these dependencies).
   - Use:
     - Time-aware cross-validation (rolling or expanding window) instead of random CV.
   - Compare against the logistic regression baseline using:
     - Brier score.
     - ROC AUC.
     - Out-of-sample season performance.

2. **Probability calibration**
   - If tree models outperform logit but are miscalibrated:
     - Apply Platt scaling or isotonic regression on a validation set.
   - Store the calibration step as part of the model pipeline so inference uses calibrated probabilities.

3. **Hyperparameter tuning**
   - Use simple, reproducible search:
     - Grid search or randomized search over a few key parameters.
   - Keep it:
     - Time-split safe.
     - Logged (so you know what settings produced a given model).

4. **Model versioning**
   - Store:
     - Model version (e.g., `v1.1.0`).
     - Training date.
     - Data cutoff date (e.g., up to games on `YYYY-MM-DD`).
   - When logging predictions, record which model version generated them.

---

## Phase 4 – Edge & Betting-Oriented Evaluation

**Goal:** Evaluate the model in terms of **edge vs the market**, not just pure accuracy.

1. **Implied vs predicted edge**
   - For each game, compute:
     - `edge = P_model(home_win) - P_implied(home_win)`
   - Track:
     - Distribution of edges by season.
     - Actual ROI if you hypothetically bet when edge > certain thresholds (e.g., 3%, 5%, 8%).

2. **Backtesting strategies**
   - Define simple betting strategies:
     - Flat stake when `edge > X%`.
     - Kelly fraction (or a capped Kelly) based on model edge.
   - Evaluate:
     - ROI per season.
     - Max drawdown.
     - Volatility and bet frequency.

3. **Calibrate risk tolerance**
   - Use backtest results to decide:
     - Reasonable edge thresholds.
     - Whether the model is overconfident in certain regimes (e.g., large underdogs).

---

## Phase 5 – Extended Capabilities (Long Term)

**Goal:** Turn the model into a more complete NBA analytics tool.

1. **In-game win probability model**
   - Use play-by-play data to build:
     - Conditional win probability given score, time, possession, and live stats.
   - Could be:
     - Separate from pre-game model.
     - Based on logistic regression or tree-based models trained on PBP snapshots.

2. **Opponent- and matchup-specific features**
   - Go beyond “team strength” to:
     - How specific matchups behave (pace interactions, three-point volume, etc.).
   - Use features like:
     - Team A’s 3P rate vs Team B’s 3P defense.
     - Offensive rebounding vs defensive rebounding.

3. **Automated retraining schedule**
   - Set up:
     - A periodic job (e.g., monthly or per season) to:
       - Ingest latest data.
       - Retrain models.
       - Evaluate against previous model.
       - Promote if performance improves.

4. **Front-end integration**
   - Build:
     - A small web UI that shows:
       - Today’s games.
       - Model probabilities.
       - Market implied probabilities.
       - Suggested edges.
   - This turns the project from “code + model” into a portfolio-ready product.

---

## Priorities Summary

If you want a rough order of attack:

1. **Now:** add closing odds features and improve evaluation metrics.
2. **Next:** integrate props and schedule strain features.
3. **Then:** try tree-based models with proper time-based validation and calibration.
4. **Finally:** build betting-oriented evaluation and, if desired, an in-game model.

Each of these steps builds on the architecture you already have and makes the project more impressive to both you and anyone who reviews your work.
