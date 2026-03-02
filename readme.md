# 2026 Fantasy Baseball Draft Dashboard

A custom, cloud-ready web application built with Python and Streamlit to manage a live 10-team fantasy baseball snake draft. 

This dashboard ingests FanGraphs projections (THE BAT X and ATC), aligns them via a master ID map, and applies custom points-league scoring. It features a robust SQLite database for transactional draft state, dynamic positional baselines, and advanced interactive data visualizations to give you a strategic edge on draft day.

---

## 🚀 Core Features

* **Snake Draft Engine:** Automatically calculates current pick, round, and team on the clock.
* **Database Backend:** Uses a local SQLite database (`draft_room.db`) to instantly and safely store draft picks. Includes an "Undo Pick" and "Reset Draft" function.
* **The "Ohtani Exception":** Custom logic seamlessly handles drafting Shohei Ohtani, assigning him dual `UTIL/SP` eligibility and merging his hitting/pitching points without costing two draft picks.
* **Live Color-Coded Draft Board:** A visual matrix showing the entire draft history, color-coded by the exact position each player was drafted to fill.
* **Custom League Settings:** Rename all 10 teams and dynamically adjust the depth of positional pools used for baseline calculations.

---

## 🧠 Advanced Metrics: VOA & RPV
The true power of this dashboard lies in its custom baseline engine, which calculates the exact replacement level for every position based on your league's specific positional depth pools.

* **VOA (Value Over Average):** A player's Total Projected Points minus the baseline average of their position. (e.g., A +40 VOA means they project to score 40 points more than the average starter at that position).
* **RPV (Replacement Positional Value):** `(VOA / Baseline) * 100`. This creates a normalized percentage, allowing you to compare the true value of a Shortstop against a Starting Pitcher.
* **Static vs. Dynamic Baselines:** In the sidebar, you can toggle how these metrics are calculated:
  * *Static:* Calculates replacement levels based on the entire player universe before the draft starts.
  * *Dynamic:* Recalculates replacement levels mid-draft based **only on the players still available**. As top Catchers leave the board, the baseline drops, causing the VOA of remaining Catchers to spike!

---

## 📊 Interactive Analytics & Visualizations
The dashboard utilizes `plotly` to render real-time, interactive charts based on the live draft state. 

### 1. The True Value Scatter Plot (Available Players Tab)
* **What it is:** A scatter plot mapping Total Points (X-axis) against RPV% (Y-axis).
* **How to use it:** Finding late-round value. Players high on the Y-axis but lower on the X-axis might not score the most raw points, but they provide massive positional leverage compared to their peers.

### 2. Positional Scarcity Cliff (Available Players Tab)
* **What it is:** A line graph showing the VOA of the top 10 remaining players at a selected position.
* **How to use it:** Identifying tier drops. If the line shows two players at +40 VOA and the third drops to -10, you know you must draft one of the top two immediately before the "cliff" falls off.

### 3. Roster Balance Analysis (Team Summaries Tab)
* **What it is:** Side-by-side donut charts comparing your selected team's Hitting vs. Pitching point distribution against the overall League Average.
* **How to use it:** Correcting draft bias. If the league is at a 55/45 split and you are at an 80/20 split, you immediately know you need to pivot to drafting pitching.

### 4. Roster Construction X-Ray (League Standings Tab)
* **What it is:** A massive horizontal stacked bar chart showing total points for every team, color-coded by the positions generating those points.
* **How to use it:** Predicting your opponents. If the team drafting directly after you has a massive Outfield color block but no Relief Pitching block, you can safely draft a Closer knowing they are unlikely to snipe the Outfielder you want.

---

## 🛠️ Setup & Local Installation

1. **Python Environment:** Ensure you have Python 3.8+ installed. 
2. **Virtual Environment (Recommended):** Open your terminal, navigate to the project folder, and create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows use: venv\Scripts\activate