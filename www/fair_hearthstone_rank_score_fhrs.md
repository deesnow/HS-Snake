# Fair Hearthstone Rank Score (FHRS)

The **Fair Hearthstone Rank Score (FHRS)** is a performance-tracking model designed to evaluate a player's season-long standing by balancing daily peaks, field density (player inflation), and overall consistency. Unlike the standard in-game ladder ranking, this system rewards players who achieve and maintain high ranks throughout the entire month.

---

## 1. Calculating the Daily Performance Score (DPS)

At the end of each day, the system records the player’s highest rank achieved ($R$) and the total number of Legend players ($N$) in that specific region.

### Formula
$$DPS = \log_{10}(N) \times \left( \frac{N - R + 1}{N} \right) \times 100$$

### Component Breakdown
* **Logarithmic Multiplier ($\log_{10}(N)$):** Serves as the **Inflation Factor**. It mathematically accounts for field density, acknowledging that securing a Top 10 rank against a field of 20,000 players at month-end requires significantly more effort and win rate than doing so against a pool of 30 players on Day 1.
* **Rank Ratio ($\frac{N - R + 1}{N}$):** Represents your percentile standing within the current active Legend pool.
* **Scaling Constant ($100$):** Used for readability, converting fractional decimals into human-friendly integer scores (e.g., displaying `40` instead of `0.4`).

---

## 2. Calculating the Season Score

The final **Season Score** is determined by the cumulative average of daily scores. To maintain parity between early grinders and late-month entrants, the divisor is strictly bound to the elapsed days of the season.

### Formula
$$Season\ Score = \frac{\sum_{d=1}^{n} DPS_d}{n}$$

### Parameters & Scoring Rules
* **$n$:** The current day of the month (e.g., on the 15th day of the month, the sum is divided by 15; at the end of a 30-day season, it is divided by 30).
* **Missing Days / Late Legend Entry:** If a player enters Legend mid-season (e.g., on Day 16), they receive **0 points** (or a minimal "Diamond baseline" score) for Days 1 through 15. This lowers their overall seasonal average, penalizing late entries and incentivizing active, high-level competition throughout the full duration of the month.

---

## Key Advantages

* **Anti-Camping Incentive:** Players cannot hit an early high rank and stop playing. Inactivity will cause their relative score to degrade as the field size ($N$) grows for active competitors.
* **Consistency Weighting:** High-peak players who experience drastic rank drop-offs will see their daily averages decline, favoring players with stable top-tier performance.
* **Dynamic Scale:** Automatically adjusts for regional player pool growth, giving late-season rank pushes appropriate mathematical value relative to field size.