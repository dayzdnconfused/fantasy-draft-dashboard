import streamlit as st
import pandas as pd
import os
import sqlite3
import requests
import plotly.express as px
import plotly.graph_objects as go

# --- PHASE 0: DATABASE INITIALIZATION ---
def init_db():
    conn = sqlite3.connect('draft_room.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS draft_picks
                 (Name TEXT, Type TEXT, Team TEXT, Position TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS teams
                 (TeamName TEXT)''')
    conn.commit()
    conn.close()

init_db()

# --- PHASE 1: BASELINE ENGINE ---
def calculate_baselines(batters, pitchers, pools):
    baselines = {}
    used_batter_names = set()
    
    if 'Pos' in batters.columns:
        for pos in ['C', '1B', '2B', '3B', 'SS', 'OF']:
            eligible = batters[batters['Pos'].astype(str).str.contains(pos, na=False)]
            top_n = eligible.nlargest(pools[pos], 'Total_Points')
            
            if not top_n.empty:
                baselines[pos] = top_n['Total_Points'].mean()
                used_batter_names.update(top_n['Name'].tolist())
            else:
                baselines[pos] = 0.001 

        remaining_batters = batters[~batters['Name'].isin(used_batter_names)]
        top_util = remaining_batters.nlargest(20, 'Total_Points')
        baselines['UTIL'] = top_util['Total_Points'].mean() if not top_util.empty else 0.001

    if 'Pos' in pitchers.columns:
        for pos in ['SP', 'RP']:
            eligible = pitchers[pitchers['Pos'].astype(str).str.contains(pos, na=False)]
            top_n = eligible.nlargest(pools[pos], 'Total_Points')
            baselines[pos] = top_n['Total_Points'].mean() if not top_n.empty else 0.001
            
    return baselines

# --- PHASE 2: DATA SETUP & ETL LOGIC ---
def fetch_fangraphs_projections():
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    try:
        bat_url = "https://www.fangraphs.com/api/projections?type=thebatx&stats=bat&pos=all&team=0&players=0&lg=all"
        bat_resp = requests.get(bat_url, headers=headers)
        if bat_resp.status_code == 200:
            pd.DataFrame(bat_resp.json()).to_csv("the_bat_x_batters.csv", index=False)
            
        pit_url = "https://www.fangraphs.com/api/projections?type=atc&stats=pit&pos=all&team=0&players=0&lg=all"
        pit_resp = requests.get(pit_url, headers=headers)
        if pit_resp.status_code == 200:
            pd.DataFrame(pit_resp.json()).to_csv("atc_pitchers.csv", index=False)
        return True
    except Exception as e:
        return False

def load_teams():
    conn = sqlite3.connect('draft_room.db')
    teams = pd.read_sql_query("SELECT TeamName FROM teams", conn)['TeamName'].tolist()
    conn.close()
    if not teams:
        return [f"Team {i}" for i in range(1, 11)]
    return teams

@st.cache_data
def load_data():
    # If the files are entirely missing, we can't run the app.
    # This prevents the ugly traceback and gives a helpful error message instead.
    if not os.path.exists("the_bat_x_batters.csv") or not os.path.exists("atc_pitchers.csv") or not os.path.exists("id_map.csv"):
        st.error("Data files missing! Please ensure the_bat_x_batters.csv, atc_pitchers.csv, and id_map.csv are pushed to your GitHub repository.")
        st.stop()
            
    batters = pd.read_csv("the_bat_x_batters.csv") 
    pitchers = pd.read_csv("atc_pitchers.csv")
    
    # Normalizing FanGraphs API JSON headers to match traditional CSV formats
    if 'PlayerName' in batters.columns:
        batters = batters.rename(columns={'PlayerName': 'Name', 'playerids': 'PlayerId'})
    if 'PlayerName' in pitchers.columns:
        pitchers = pitchers.rename(columns={'PlayerName': 'Name', 'playerids': 'PlayerId'})
        
    id_map = pd.read_csv("id_map.csv")
    id_map = id_map.rename(columns={'IDFANGRAPHS': 'PlayerId', 'ALLPOS': 'Pos'})
    id_map['Pos'] = id_map['Pos'].str.upper()
    id_map['Pos'] = id_map['Pos'].str.replace(r'\bP\b', 'SP', regex=True)
    id_map_clean = id_map[['PlayerId', 'Pos']].drop_duplicates(subset=['PlayerId'])
    
    batters = pd.merge(batters, id_map_clean, on='PlayerId', how='left')
    pitchers = pd.merge(pitchers, id_map_clean, on='PlayerId', how='left')

    if all(col in batters.columns for col in ['H', '2B', '3B', 'HR']):
        batters['TB'] = batters['H'] + batters['2B'] + (2 * batters['3B']) + (3 * batters['HR'])
    
    k_col_b = 'K' if 'K' in batters.columns else 'SO'
    k_col_p = 'K' if 'K' in pitchers.columns else 'SO'
    
    batters['Total_Points'] = (
        batters['R'] * 1 + batters.get('TB', 0) * 1 + batters['RBI'] * 1 +
        batters['BB'] * 1 + batters[k_col_b] * -1 + batters['SB'] * 1
    )
    
    pitchers['Total_Points'] = (
        pitchers['IP'] * 3 + pitchers['H'] * -1 + pitchers['ER'] * -2 +
        pitchers['BB'] * -1 + pitchers[k_col_p] * 1 + pitchers.get('QS', 0) * 1 +
        pitchers['W'] * 2 + pitchers['L'] * -2 + pitchers.get('SV', 0) * 5 + pitchers.get('HLD', 0) * 2
    )

    batters['Weekly_Avg'] = batters['Total_Points'] / 24
    pitchers['Weekly_Avg'] = pitchers['Total_Points'] / 24
    
    batters['Drafted_By'] = "Available"
    pitchers['Drafted_By'] = "Available"
    batters['Drafted_Pos'] = None
    pitchers['Drafted_Pos'] = None
    
    conn = sqlite3.connect('draft_room.db')
    state_df = pd.read_sql_query("SELECT * FROM draft_picks", conn)
    conn.close()
            
    for index, row in state_df.iterrows():
        if row['Type'] == 'Two-Way' or row['Name'] == 'Shohei Ohtani':
            batters.loc[batters['Name'] == row['Name'], 'Drafted_By'] = row['Team']
            batters.loc[batters['Name'] == row['Name'], 'Drafted_Pos'] = 'UTIL'
            pitchers.loc[pitchers['Name'] == row['Name'], 'Drafted_By'] = row['Team']
            pitchers.loc[pitchers['Name'] == row['Name'], 'Drafted_Pos'] = 'SP'
        elif row['Type'] == 'Batter':
            batters.loc[batters['Name'] == row['Name'], 'Drafted_By'] = row['Team']
            batters.loc[batters['Name'] == row['Name'], 'Drafted_Pos'] = row['Position']
        else:
            pitchers.loc[pitchers['Name'] == row['Name'], 'Drafted_By'] = row['Team']
            pitchers.loc[pitchers['Name'] == row['Name'], 'Drafted_Pos'] = row['Position']
                
    return batters, pitchers

# --- PHASE 3: BUILDING THE UI ---
st.set_page_config(layout="wide")
st.title("2026 Fantasy Baseball Draft Room")

if 'teams' not in st.session_state:
    st.session_state.teams = load_teams()

if 'batters' not in st.session_state:
    st.session_state.batters, st.session_state.pitchers = load_data()

# Global Data Variables for cross-tab calculations
drafted_batters = st.session_state.batters[st.session_state.batters['Drafted_By'] != "Available"]
drafted_pitchers = st.session_state.pitchers[st.session_state.pitchers['Drafted_By'] != "Available"]

# --- SNAKE DRAFT CALCULATOR ---
conn = sqlite3.connect('draft_room.db')
c = conn.cursor()
c.execute("SELECT COUNT(*) FROM draft_picks")
total_drafted = c.fetchone()[0]
conn.close()

num_teams = len(st.session_state.teams)
current_round = (total_drafted // num_teams) + 1
pick_in_round = total_drafted % num_teams

if current_round % 2 != 0:
    team_on_clock_idx = pick_in_round
else:
    team_on_clock_idx = (num_teams - 1) - pick_in_round

# --- SIDEBAR: DRAFT CONTROLS ---
st.sidebar.header(f"Draft Room - Pick {total_drafted + 1}")
st.sidebar.markdown(f"**Round {current_round} | On the Clock:**")

player_type = st.sidebar.radio("Player Type", ["Batter", "Pitcher"], horizontal=True, key="player_type_radio")

selected_team = st.sidebar.selectbox("Selecting Team", st.session_state.teams, index=team_on_clock_idx)
player_type = st.sidebar.radio("Player Type", ["Batter", "Pitcher"], horizontal=True)

if player_type == "Batter":
    available_players = st.session_state.batters[st.session_state.batters['Drafted_By'] == "Available"]['Name'].tolist()
else:
    available_players = st.session_state.pitchers[st.session_state.pitchers['Drafted_By'] == "Available"]['Name'].tolist()

selected_player = st.sidebar.selectbox("Player", available_players)

if selected_player:
    if player_type == "Batter":
        raw_pos = st.session_state.batters.loc[st.session_state.batters['Name'] == selected_player, 'Pos'].values[0]
        pos_options = str(raw_pos).split('/') + ['UTIL']
    else:
        raw_pos = st.session_state.pitchers.loc[st.session_state.pitchers['Name'] == selected_player, 'Pos'].values[0]
        pos_options = str(raw_pos).split('/')
        
    selected_pos = st.sidebar.selectbox("Draft As (Position)", pos_options)

if st.sidebar.button("Draft Player", type="primary"):
    if selected_player == "Shohei Ohtani":
        st.session_state.batters.loc[st.session_state.batters['Name'] == selected_player, 'Drafted_By'] = selected_team
        st.session_state.batters.loc[st.session_state.batters['Name'] == selected_player, 'Drafted_Pos'] = "UTIL"
        st.session_state.pitchers.loc[st.session_state.pitchers['Name'] == selected_player, 'Drafted_By'] = selected_team
        st.session_state.pitchers.loc[st.session_state.pitchers['Name'] == selected_player, 'Drafted_Pos'] = "SP"
        record_type, record_pos = "Two-Way", "UTIL/SP"
    else:
        if player_type == "Batter":
            st.session_state.batters.loc[st.session_state.batters['Name'] == selected_player, 'Drafted_By'] = selected_team
            st.session_state.batters.loc[st.session_state.batters['Name'] == selected_player, 'Drafted_Pos'] = selected_pos
        else:
            st.session_state.pitchers.loc[st.session_state.pitchers['Name'] == selected_player, 'Drafted_By'] = selected_team
            st.session_state.pitchers.loc[st.session_state.pitchers['Name'] == selected_player, 'Drafted_Pos'] = selected_pos
        record_type, record_pos = player_type, selected_pos
        
    conn = sqlite3.connect('draft_room.db')
    c = conn.cursor()
    c.execute("INSERT INTO draft_picks VALUES (?, ?, ?, ?)", (selected_player, record_type, selected_team, record_pos))
    conn.commit()
    conn.close()
        
    st.sidebar.success(f"{selected_player} drafted to {selected_team} as {record_pos}")
    st.session_state.main_view_radio = "Pitchers" if player_type == "Pitcher" else "Batters"
    st.rerun() 

st.sidebar.markdown("---")

# --- DRAFT MANAGEMENT ---
with st.sidebar.expander("Draft Management (Undo/Reset)"):
    if st.button("Undo Last Pick"):
        conn = sqlite3.connect('draft_room.db')
        c = conn.cursor()
        c.execute("SELECT rowid, Name FROM draft_picks ORDER BY rowid DESC LIMIT 1")
        last_pick = c.fetchone()
        
        if last_pick:
            row_id, player_name = last_pick
            c.execute("DELETE FROM draft_picks WHERE rowid = ?", (row_id,))
            conn.commit()
            st.cache_data.clear()
            st.session_state.batters, st.session_state.pitchers = load_data()
            st.success(f"Successfully undid pick: {player_name}")
        else:
            st.warning("No picks to undo.")
        conn.close()
        st.rerun()

    st.markdown("---")
    
    if st.button("🧨 Reset Entire Draft"):
        conn = sqlite3.connect('draft_room.db')
        c = conn.cursor()
        c.execute("DELETE FROM draft_picks")
        conn.commit()
        conn.close()
        st.cache_data.clear()
        st.session_state.batters, st.session_state.pitchers = load_data()
        st.success("Draft completely reset!")
        st.rerun()

# --- LEAGUE SETTINGS ---
st.sidebar.header("League Settings")
with st.sidebar.expander("Customize Team Names"):
    st.warning("Only edit before the draft begins!")
    new_teams = []
    for i, default_name in enumerate(st.session_state.teams):
        new_teams.append(st.text_input(f"Team {i+1}", value=default_name))
        
    if st.button("Save Team Names"):
        conn = sqlite3.connect('draft_room.db')
        c = conn.cursor()
        c.execute("DELETE FROM teams")
        for t in new_teams:
            c.execute("INSERT INTO teams VALUES (?)", (t,))
        conn.commit()
        conn.close()
        st.session_state.teams = new_teams
        st.success("Teams saved!")
        st.rerun()

baseline_mode = st.sidebar.radio("RPV Baseline Calculation", ["Static (Pre-Draft)", "Dynamic (Available Only)"])

with st.sidebar.expander("Adjust Positional Pools"):
    c_pool = st.number_input("Catcher (C)", value=10, step=1)
    b1_pool = st.number_input("1st Base (1B)", value=15, step=1)
    b2_pool = st.number_input("2nd Base (2B)", value=15, step=1)
    b3_pool = st.number_input("3rd Base (3B)", value=15, step=1)
    ss_pool = st.number_input("Shortstop (SS)", value=15, step=1)
    of_pool = st.number_input("Outfield (OF)", value=40, step=1)
    sp_pool = st.number_input("Starting Pitcher (SP)", value=90, step=1)
    rp_pool = st.number_input("Relief Pitcher (RP)", value=30, step=1)

current_pools = {
    'C': c_pool, '1B': b1_pool, '2B': b2_pool, '3B': b3_pool, 
    'SS': ss_pool, 'OF': of_pool, 'SP': sp_pool, 'RP': rp_pool
}

if baseline_mode == "Dynamic (Available Only)":
    avail_batters = st.session_state.batters[st.session_state.batters['Drafted_By'] == 'Available']
    avail_pitchers = st.session_state.pitchers[st.session_state.pitchers['Drafted_By'] == 'Available']
    baselines = calculate_baselines(avail_batters, avail_pitchers, current_pools)
else:
    baselines = calculate_baselines(st.session_state.batters, st.session_state.pitchers, current_pools)

with st.sidebar.expander("ETL & Data Sync"):
    if st.button("Download Latest Projections"):
        with st.spinner("Fetching from FanGraphs API..."):
            success = fetch_fangraphs_projections()
            if success:
                st.cache_data.clear()
                st.session_state.batters, st.session_state.pitchers = load_data()
                st.success("Projections Synced & Reloaded!")
            else:
                st.error("Failed to fetch data. Check your connection.")
    
    if st.button("Refresh Code Cache"):
        st.cache_data.clear()
        st.session_state.batters, st.session_state.pitchers = load_data()
        st.success("Cache Cleared!")

# --- MAIN DASHBOARD ---
tab1, tab2, tab3, tab4 = st.tabs(["Available Players", "Team Rosters", "League Standings", "Draft Board"])

with tab1:
    st.header("Available Projections")
    if "main_view_radio" not in st.session_state:
        st.session_state.main_view_radio = "Batters"
        
    view_type = st.radio("View", ["Batters", "Pitchers"], horizontal=True, key="main_view_radio")
    
    if view_type == "Batters":
        df = st.session_state.batters[st.session_state.batters['Drafted_By'] == "Available"].copy()
        if 'Pos' in df.columns:
            positions = ["All", "C", "1B", "2B", "3B", "SS", "OF", "UTIL"]
            col1, col2 = st.columns([1, 4])
            with col1:
                pos_filter = st.selectbox("Filter Position", positions)
            
            if pos_filter != "All" and pos_filter != "UTIL":
                df = df[df['Pos'].astype(str).str.contains(pos_filter, na=False)]
                
            active_baseline = baselines.get('UTIL', 0.001) if pos_filter == "All" else baselines.get(pos_filter, 0.001)
            df['VOA'] = df['Total_Points'] - active_baseline
            df['RPV'] = (df['VOA'] / active_baseline) * 100 
            
            # --- VIS 1: TRUE VALUE SCATTER PLOT ---
            with st.expander("📊 View True Value Scatter Plot (Top 150)", expanded=False):
                scatter_df = df.sort_values('VOA', ascending=False).head(150)
                fig_scatter = px.scatter(scatter_df, x="Total_Points", y="RPV", color="Pos", hover_name="Name", 
                                         title=f"Total Points vs. RPV% (Available {pos_filter} Batters)",
                                         labels={"Total_Points": "Total Projected Points", "RPV": "RPV (%)"})
                fig_scatter.update_layout(height=400)
                st.plotly_chart(fig_scatter, use_container_width=True)
            
            # --- VIS 2: SCARCITY CLIFF ---
            with st.expander(f"📉 View Positional Scarcity Cliff ({pos_filter})", expanded=True):
                top_10 = df.sort_values('VOA', ascending=False).head(10)
                fig_cliff = px.line(top_10, x="Name", y="VOA", markers=True, 
                                    title=f"Top 10 Remaining {pos_filter} - VOA Drop-off",
                                    labels={"Name": "Player", "VOA": "Value Over Average"})
                fig_cliff.update_traces(line_color='#00b4d8', marker=dict(size=10, color='#0077b6'))
                st.plotly_chart(fig_cliff, use_container_width=True)
            
            k_col = 'K' if 'K' in df.columns else 'SO'
            display_cols = ['Name', 'Team', 'Pos', 'VOA', 'RPV', 'R', 'TB', 'RBI', 'BB', k_col, 'SB', 'Total_Points', 'Weekly_Avg']
            st.dataframe(df[display_cols].sort_values(by="VOA", ascending=False),
                         column_config={"VOA": st.column_config.NumberColumn("VOA (+/-)", format="%.1f"), "RPV": st.column_config.NumberColumn("RPV", format="%.1f%%")},
                         hide_index=True)
    else:
        df = st.session_state.pitchers[st.session_state.pitchers['Drafted_By'] == "Available"].copy()
        if 'Pos' in df.columns:
            positions = ["All", "SP", "RP"]
            col1, col2 = st.columns([1, 4])
            with col1:
                pos_filter = st.selectbox("Filter Pitcher Position", positions)
            
            if pos_filter != "All":
                 df = df[df['Pos'].astype(str).str.contains(pos_filter, na=False)] 
            
            active_baseline = baselines.get('SP', 0.001) if pos_filter == "All" else baselines.get(pos_filter, 0.001)
            df['VOA'] = df['Total_Points'] - active_baseline
            df['RPV'] = (df['VOA'] / active_baseline) * 100
            
            # --- VIS 1: TRUE VALUE SCATTER PLOT ---
            with st.expander("📊 View True Value Scatter Plot (Top 100)", expanded=False):
                scatter_df = df.sort_values('VOA', ascending=False).head(100)
                fig_scatter = px.scatter(scatter_df, x="Total_Points", y="RPV", color="Pos", hover_name="Name", 
                                         title=f"Total Points vs. RPV% (Available {pos_filter} Pitchers)",
                                         labels={"Total_Points": "Total Projected Points", "RPV": "RPV (%)"})
                fig_scatter.update_layout(height=400)
                st.plotly_chart(fig_scatter, use_container_width=True)
            
            # --- VIS 2: SCARCITY CLIFF ---
            with st.expander(f"📉 View Positional Scarcity Cliff ({pos_filter})", expanded=True):
                top_10 = df.sort_values('VOA', ascending=False).head(10)
                fig_cliff = px.line(top_10, x="Name", y="VOA", markers=True, 
                                    title=f"Top 10 Remaining {pos_filter} - VOA Drop-off",
                                    labels={"Name": "Player", "VOA": "Value Over Average"})
                fig_cliff.update_traces(line_color='#ff9f1c', marker=dict(size=10, color='#e07a5f'))
                st.plotly_chart(fig_cliff, use_container_width=True)

            k_col = 'K' if 'K' in df.columns else 'SO'
            display_cols = ['Name', 'Team', 'Pos', 'VOA', 'RPV', 'IP', 'H', 'ER', 'BB', k_col, 'QS', 'W', 'L', 'SV', 'HLD', 'Total_Points', 'Weekly_Avg']
            display_cols = [col for col in display_cols if col in df.columns]
            st.dataframe(df[display_cols].sort_values(by="VOA", ascending=False),
                         column_config={"VOA": st.column_config.NumberColumn("VOA (+/-)", format="%.1f"), "RPV": st.column_config.NumberColumn("RPV", format="%.1f%%")},
                         hide_index=True)

with tab2:
    st.header("Team Summaries & Analysis")
    team_view = st.selectbox("Select Team to View", st.session_state.teams)
    
    team_batters = st.session_state.batters[st.session_state.batters['Drafted_By'] == team_view]
    team_pitchers = st.session_state.pitchers[st.session_state.pitchers['Drafted_By'] == team_view]
    
    total_proj_points = team_batters['Total_Points'].sum() + team_pitchers['Total_Points'].sum()
    total_weekly_avg = team_batters['Weekly_Avg'].sum() + team_pitchers['Weekly_Avg'].sum()
    
    col1, col2 = st.columns(2)
    col1.metric("Projected Total Points", f"{total_proj_points:.2f}")
    col2.metric("Projected Weekly Average", f"{total_weekly_avg:.2f}")
    
    # --- VIS 3: HITTING VS PITCHING BALANCE GAUGE ---
    st.markdown("---")
    st.subheader("Roster Balance Analysis")
    
    # Calculate League Totals
    league_bat_pts = drafted_batters['Total_Points'].sum() if not drafted_batters.empty else 0
    league_pit_pts = drafted_pitchers['Total_Points'].sum() if not drafted_pitchers.empty else 0
    league_total = league_bat_pts + league_pit_pts
    
    # Calculate Selected Team Totals
    team_bat_pts = team_batters['Total_Points'].sum()
    team_pit_pts = team_pitchers['Total_Points'].sum()
    
    col_chart1, col_chart2 = st.columns(2)
    with col_chart1:
        if total_proj_points > 0:
            fig_team = go.Figure(data=[go.Pie(labels=['Hitting', 'Pitching'], values=[team_bat_pts, team_pit_pts], hole=.4, marker_colors=['#4361ee', '#f72585'])])
            fig_team.update_layout(title_text=f"{team_view} Point Split", title_x=0.5)
            st.plotly_chart(fig_team, use_container_width=True)
        else:
            st.info(f"Draft players to {team_view} to see balance.")
            
    with col_chart2:
        if league_total > 0:
            fig_lg = go.Figure(data=[go.Pie(labels=['Hitting', 'Pitching'], values=[league_bat_pts, league_pit_pts], hole=.4, marker_colors=['#4361ee', '#f72585'])])
            fig_lg.update_layout(title_text="League Average Point Split", title_x=0.5)
            st.plotly_chart(fig_lg, use_container_width=True)
        else:
            st.info("Draft players to see the league average balance.")
    st.markdown("---")

    st.subheader("Hitters")
    hitter_display = ['Name', 'Drafted_Pos', 'Total_Points', 'Weekly_Avg'] if 'Drafted_Pos' in team_batters.columns else ['Name', 'Total_Points', 'Weekly_Avg']
    st.dataframe(team_batters[hitter_display], hide_index=True)
    
    st.subheader("Pitchers")
    pitcher_display = ['Name', 'Drafted_Pos', 'Total_Points', 'Weekly_Avg'] if 'Drafted_Pos' in team_pitchers.columns else ['Name', 'Total_Points', 'Weekly_Avg']
    st.dataframe(team_pitchers[pitcher_display], hide_index=True)

with tab3:
    st.header("Live League Standings")
    
    if drafted_batters.empty and drafted_pitchers.empty:
        st.info("No players have been drafted yet.")
    else:
        # --- VIS 4: LEAGUE-WIDE POINT ALLOCATION (THE ROSTER X-RAY) ---
        st.subheader("Roster Construction X-Ray")
        dbat = drafted_batters[['Drafted_By', 'Drafted_Pos', 'Total_Points']].copy()
        dpit = drafted_pitchers[['Drafted_By', 'Drafted_Pos', 'Total_Points']].copy()
        alloc_df = pd.concat([dbat, dpit])
        
        # Group by Team and Drafted_Pos for the stacked bar
        alloc_grouped = alloc_df.groupby(['Drafted_By', 'Drafted_Pos'])['Total_Points'].sum().reset_index()
        
        fig_alloc = px.bar(alloc_grouped, y='Drafted_By', x='Total_Points', color='Drafted_Pos', 
                           orientation='h', title="Projected Points by Position per Team",
                           labels={'Drafted_By': 'Team', 'Total_Points': 'Total Projected Points'})
        fig_alloc.update_layout(barmode='stack', yaxis={'categoryorder':'total ascending'}, height=500)
        st.plotly_chart(fig_alloc, use_container_width=True)
        st.markdown("---")

        b_standings = pd.DataFrame()
        if not drafted_batters.empty:
            b_standings = drafted_batters.groupby('Drafted_By')[['Total_Points', 'Weekly_Avg']].sum().reset_index()
            
        p_standings = pd.DataFrame()
        if not drafted_pitchers.empty:
            p_standings = drafted_pitchers.groupby('Drafted_By')[['Total_Points', 'Weekly_Avg']].sum().reset_index()
            
        combined = pd.concat([b_standings, p_standings])
        league_standings = combined.groupby('Drafted_By')[['Total_Points', 'Weekly_Avg']].sum().reset_index()
        league_standings = league_standings.rename(columns={'Drafted_By': 'Team', 'Total_Points': 'Proj Total Points', 'Weekly_Avg': 'Proj Weekly Avg'})
        st.dataframe(
            league_standings.sort_values(by='Proj Total Points', ascending=False),
            column_config={"Proj Total Points": st.column_config.NumberColumn(format="%.2f"), "Proj Weekly Avg": st.column_config.NumberColumn(format="%.2f")},
            hide_index=True, use_container_width=True 
        )

with tab4:
    st.header("Draft Board")
    conn = sqlite3.connect('draft_room.db')
    draft_df = pd.read_sql_query("SELECT * FROM draft_picks", conn)
    conn.close()
    
    if not draft_df.empty:
        board = pd.DataFrame(index=range(1, 22), columns=st.session_state.teams)
        
        for team in st.session_state.teams:
            team_picks = draft_df[draft_df['Team'] == team]
            for i, (_, row) in enumerate(team_picks.iterrows()):
                board.at[i+1, team] = f"{row['Name']} ({row['Position']})"
        
        def color_board(val):
            if pd.isna(val) or val == "": return ''
            if 'UTIL/SP' in val: return 'background-color: #ffd700; color: black;' 
            elif '(C)' in val: return 'background-color: #ffb3ba; color: black;'
            elif '(1B)' in val: return 'background-color: #ffdfba; color: black;'
            elif '(2B)' in val: return 'background-color: #ffffba; color: black;'
            elif '(3B)' in val: return 'background-color: #baffc9; color: black;'
            elif '(SS)' in val: return 'background-color: #bae1ff; color: black;'
            elif '(OF)' in val: return 'background-color: #e6b3ff; color: black;'
            elif '(UTIL)' in val: return 'background-color: #d9d9d9; color: black;'
            elif '(SP)' in val: return 'background-color: #f0e68c; color: black;'
            elif '(RP)' in val: return 'background-color: #ffb3e6; color: black;'
            return ''
            
        st.dataframe(board.style.map(color_board), use_container_width=True)
    else:
        st.info("The draft board will appear here once the first pick is made.")