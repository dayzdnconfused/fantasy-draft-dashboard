import streamlit as st
import pandas as pd
import os
import psycopg2
import requests
import plotly.express as px
import plotly.graph_objects as go
import warnings

# Suppress pandas warning about using raw psycopg2 connections
warnings.filterwarnings('ignore', category=UserWarning)

def get_db_connection():
    """Establishes a connection to the PostgreSQL database using Streamlit Secrets."""
    return psycopg2.connect(st.secrets["DATABASE_URL"])

# --- PHASE 0: DATABASE INITIALIZATION ---
@st.cache_resource
def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS draft_picks
                 (id SERIAL PRIMARY KEY, Name TEXT, Type TEXT, Team TEXT, Position TEXT)''')
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

@st.cache_data(ttl=3600)
def fetch_official_injury_status():
    try:
        url = "https://statsapi.mlb.com/api/v1/teams?sportId=1&hydrate=roster(rosterType=fullRoster)"
        headers = {'User-Agent': 'Mozilla/5.0'}
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            injury_list = []
            for team in data.get("teams", []):
                roster_data = team.get("roster", {}).get("roster", [])
                for player in roster_data:
                    status_dict = player.get("status", {})
                    status_code = status_dict.get("code", "")
                    status_desc = status_dict.get("description", "")
                    
                    if status_code.startswith('D') or status_code == 'O' or 'IL' in status_desc:
                        name = player.get("person", {}).get("fullName", "")
                        injury_list.append({"Name": name, "Injury_Status": status_desc})
                        
            df = pd.DataFrame(injury_list)
            if not df.empty:
                return df.drop_duplicates(subset=['Name'])
    except Exception as e:
        # NEW: We are no longer using 'pass' to silently swallow the error.
        # This will blast the exact network or parsing error to the UI.
        st.error(f"MLB API Error: {e}") 
        
    return pd.DataFrame(columns=['Name', 'Injury_Status'])

@st.cache_data
def load_teams():
    conn = get_db_connection()
    teams = pd.read_sql_query("SELECT TeamName FROM teams", conn)['teamname'].tolist()
    conn.close()
    if not teams:
        return [f"Team {i}" for i in range(1, 11)]
    return teams

@st.cache_data
def load_base_data():
    """Heavy lifting: Parses CSVs, merges injuries, and calculates math. Cached for speed."""
    if not os.path.exists("the_bat_x_batters.csv") or not os.path.exists("atc_pitchers.csv") or not os.path.exists("id_map.csv"):
        st.error("Data files missing! Please ensure the_bat_x_batters.csv, atc_pitchers.csv, and id_map.csv are pushed to your GitHub repository.")
        st.stop()
            
    batters = pd.read_csv("the_bat_x_batters.csv") 
    pitchers = pd.read_csv("atc_pitchers.csv")
    
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

    injury_df = fetch_official_injury_status()
    
    if not injury_df.empty:
        import unicodedata
        def normalize_name(name):
            if pd.isna(name): return ""
            name = unicodedata.normalize('NFKD', str(name)).encode('ASCII', 'ignore').decode('utf-8')
            return name.lower().strip().replace('.', '').replace("'", "").replace("-", " ")

        batters['Merge_Name'] = batters['Name'].apply(normalize_name)
        pitchers['Merge_Name'] = pitchers['Name'].apply(normalize_name)
        injury_df['Merge_Name'] = injury_df['Name'].apply(normalize_name)
        
        batters = pd.merge(batters, injury_df[['Merge_Name', 'Injury_Status']], on='Merge_Name', how='left').drop(columns=['Merge_Name'])
        pitchers = pd.merge(pitchers, injury_df[['Merge_Name', 'Injury_Status']], on='Merge_Name', how='left').drop(columns=['Merge_Name'])
        
        batters['Injury_Status'] = batters['Injury_Status'].fillna("")
        pitchers['Injury_Status'] = pitchers['Injury_Status'].fillna("")
    else:
        batters['Injury_Status'] = ""
        pitchers['Injury_Status'] = ""

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
    
    return batters, pitchers

def sync_draft_state(batters, pitchers):
    """Live Sync: Queries Postgres to update drafted players. NO cache decorator!"""
    conn = get_db_connection()
    state_df = pd.read_sql_query("SELECT * FROM draft_picks", conn)
    conn.close()
            
    for index, row in state_df.iterrows():
        name = row['name']
        team = row['team']
        pos = row['position']
        row_type = row['type']
        
        if row_type == 'Two-Way' or name == 'Shohei Ohtani':
            batters.loc[batters['Name'] == name, 'Drafted_By'] = team
            batters.loc[batters['Name'] == name, 'Drafted_Pos'] = 'UTIL'
            pitchers.loc[pitchers['Name'] == name, 'Drafted_By'] = team
            pitchers.loc[pitchers['Name'] == name, 'Drafted_Pos'] = 'SP'
        elif row_type == 'Batter':
            batters.loc[batters['Name'] == name, 'Drafted_By'] = team
            batters.loc[batters['Name'] == name, 'Drafted_Pos'] = pos
        else:
            pitchers.loc[pitchers['Name'] == name, 'Drafted_By'] = team
            pitchers.loc[pitchers['Name'] == name, 'Drafted_Pos'] = pos
                
    return batters, pitchers

# --- PHASE 3: BUILDING THE UI ---
st.set_page_config(layout="wide")
st.title("2026 Fantasy Baseball Draft Room")

if 'teams' not in st.session_state:
    st.session_state.teams = load_teams()

base_batters, base_pitchers = load_base_data()
st.session_state.batters, st.session_state.pitchers = sync_draft_state(base_batters.copy(), base_pitchers.copy())

drafted_batters = st.session_state.batters[st.session_state.batters['Drafted_By'] != "Available"]
drafted_pitchers = st.session_state.pitchers[st.session_state.pitchers['Drafted_By'] != "Available"]

# --- SNAKE DRAFT CALCULATOR ---
conn = get_db_connection()
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

selected_team = st.sidebar.selectbox("Selecting Team", st.session_state.teams, index=team_on_clock_idx)

if "player_type_radio" not in st.session_state:
    st.session_state.player_type_radio = "Batter"
player_type = st.sidebar.radio("Player Type", ["Batter", "Pitcher"], horizontal=True, key="player_type_radio")

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
        pos_options = str(raw_pos).split('/') + ['P'] # <-- NEW: Append 'P' to pitcher options
        
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
        
    conn = get_db_connection()
    c = conn.cursor()
    # Postgres uses %s instead of ? for parameter insertion
    c.execute("INSERT INTO draft_picks (Name, Type, Team, Position) VALUES (%s, %s, %s, %s)", 
              (selected_player, record_type, selected_team, record_pos))
    conn.commit()
    conn.close()
        
    st.sidebar.success(f"{selected_player} drafted to {selected_team} as {record_pos}")
    st.session_state.main_view_radio = "Pitchers" if player_type == "Pitcher" else "Batters"
    st.rerun() 

st.sidebar.markdown("---")

# --- DRAFT MANAGEMENT ---
with st.sidebar.expander("Draft Management (Undo/Reset)"):
    
    # --- 1-CLICK CSV BACKUP ---
    st.markdown("**Backup Draft State**")
    conn = get_db_connection()
    backup_df = pd.read_sql_query("SELECT * FROM draft_picks ORDER BY id ASC", conn)
    conn.close()
    
    if not backup_df.empty:
        csv_backup = backup_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="💾 Download Draft Backup (CSV)",
            data=csv_backup,
            file_name=f"draft_backup_pick_{total_drafted}.csv",
            mime="text/csv",
            type="primary",
            help="Downloads a permanent CSV file of every pick made so far."
        )
    else:
        st.info("No picks to backup yet.")
        
    st.markdown("---")

    # --- RESTORE FROM CSV ---
    st.markdown("**Restore Draft State**")
    uploaded_file = st.file_uploader("Upload a previous CSV backup", type=["csv"])
    
    if uploaded_file is not None:
        if st.button("⚠️ Restore from Backup", type="primary"):
            try:
                restore_df = pd.read_csv(uploaded_file)
                # Normalize columns to lowercase to match Postgres output
                restore_df.columns = [col.lower() for col in restore_df.columns]
                
                required_cols = {'name', 'type', 'team', 'position'}
                if required_cols.issubset(set(restore_df.columns)):
                    conn = get_db_connection()
                    c = conn.cursor()
                    
                    # 1. Wipe the current corrupted/empty board
                    c.execute("DELETE FROM draft_picks")
                    
                    # 2. Re-insert the historical picks in exact chronological order
                    for _, row in restore_df.iterrows():
                        c.execute("INSERT INTO draft_picks (Name, Type, Team, Position) VALUES (%s, %s, %s, %s)",
                                  (row['name'], row['type'], row['team'], row['position']))
                        
                    conn.commit()
                    conn.close()
                    
                    st.success("Draft successfully restored!")
                    st.rerun()
                else:
                    st.error("Invalid CSV format. Missing required columns.")
            except Exception as e:
                st.error(f"Error restoring draft: {e}")

    st.markdown("---")
    
    # --- UNDO LOGIC ---
    if st.button("Undo Last Pick"):
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT id, Name FROM draft_picks ORDER BY id DESC LIMIT 1")
        last_pick = c.fetchone()
        
        if last_pick:
            row_id, player_name = last_pick
            c.execute("DELETE FROM draft_picks WHERE id = %s", (row_id,))
            conn.commit()
            st.success(f"Successfully undid pick: {player_name}")
        else:
            st.warning("No picks to undo.")
        conn.close()
        st.rerun()

    st.markdown("---")
    
    # --- RESET LOGIC ---
    if st.button("🧨 Reset Entire Draft"):
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("DELETE FROM draft_picks")
        conn.commit()
        conn.close()
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
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("DELETE FROM teams")
        for t in new_teams:
            c.execute("INSERT INTO teams (TeamName) VALUES (%s)", (t,))
        conn.commit()
        conn.close()
        
        load_teams.clear() # <--- NEW: Forces the app to read the updated names
        
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
                # NEW: Use the two-step load and sync process
                base_batters, base_pitchers = load_base_data()
                st.session_state.batters, st.session_state.pitchers = sync_draft_state(base_batters.copy(), base_pitchers.copy())
                st.success("Projections Synced & Reloaded!")
            else:
                st.error("Failed to fetch data. Check your connection.")
    
    if st.button("Refresh Code Cache"):
        st.cache_data.clear()
        # NEW: Use the two-step load and sync process
        base_batters, base_pitchers = load_base_data()
        st.session_state.batters, st.session_state.pitchers = sync_draft_state(base_batters.copy(), base_pitchers.copy())
        st.success("Cache Cleared!")

# --- ROSTER LIMITS & BENCH LOGIC ---
ROSTER_LIMITS = {
    'C': 1, '1B': 1, '2B': 1, '3B': 1, 'SS': 1, 'OF': 3, 'UTIL': 1, 
    'SP': 99,  # Forces all SPs to be active "Starters" in the points calculation
    'RP': 3, 
    'P': 1,    # NEW: The Pitcher flex slot
    'UTIL/SP': 1 
}

def assign_roster_status(df, limits):
    """Sorts players by points, fills starting roster limits, and pushes the rest to the bench."""
    if df.empty:
        df['Roster_Status'] = 'Starter'
        return df
    
    df = df.copy()
    # Sort highest points first so the best players get the starting slots
    df = df.sort_values(by='Total_Points', ascending=False)
    df['Roster_Status'] = 'Bench'
    
    for team in df['Drafted_By'].unique():
        for pos, limit in limits.items():
            mask = (df['Drafted_By'] == team) & (df['Drafted_Pos'] == pos)
            # Take the top N players for this position and flag them as Starters
            starters_idx = df[mask].head(limit).index
            df.loc[starters_idx, 'Roster_Status'] = 'Starter'
            
    return df

# Apply bench logic to all currently drafted players
drafted_batters = assign_roster_status(drafted_batters, ROSTER_LIMITS)
drafted_pitchers = assign_roster_status(drafted_pitchers, ROSTER_LIMITS)

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
            # --- FIX 1: DYNAMIC SCATTER PLOT ---
            # Determine Primary Position (e.g., 'C' from 'C/1B') for accurate baseline mapping
            df['Primary_Pos'] = df['Pos'].astype(str).str.split('/').str[0]
            df['Player_Baseline'] = df['Primary_Pos'].map(baselines).fillna(baselines.get('UTIL', 0.001))
            
            # Calculate True VOA and RPV based on their specific positional baseline
            df['VOA'] = df['Total_Points'] - df['Player_Baseline']
            df['RPV'] = (df['VOA'] / df['Player_Baseline']) * 100
            
            positions = ["All", "C", "1B", "2B", "3B", "SS", "OF", "UTIL"]
            col1, col2 = st.columns([1, 4])
            with col1:
                pos_filter = st.selectbox("Filter Position", positions)
            
            if pos_filter != "All" and pos_filter != "UTIL":
                df = df[df['Pos'].astype(str).str.contains(pos_filter, na=False)]
            
            with st.expander("📊 View True Value Scatter Plot (Top 150)", expanded=False):
                scatter_df = df.sort_values('VOA', ascending=False).head(150)
                fig_scatter = px.scatter(scatter_df, x="Total_Points", y="RPV", color="Primary_Pos", hover_name="Name", 
                                         title=f"Total Points vs. RPV% (Available {pos_filter} Batters)",
                                         labels={"Total_Points": "Total Projected Points", "RPV": "RPV (%)", "Primary_Pos": "Primary Position"})
                fig_scatter.update_layout(height=400)
                st.plotly_chart(fig_scatter, use_container_width=True)
            
            # --- FIX 2: BIRD'S-EYE CLIFF CHART ---
            with st.expander(f"📉 View Bird's-Eye Positional Scarcity Cliff", expanded=True):
                cliff_data = {}
                target_positions = ['C', '1B', '2B', '3B', 'SS', 'OF'] if pos_filter == "All" or pos_filter == "UTIL" else [pos_filter]
                
                for pos in target_positions:
                    pos_df = st.session_state.batters[(st.session_state.batters['Drafted_By'] == "Available") & (st.session_state.batters['Pos'].astype(str).str.contains(pos, na=False))].copy()
                    pos_df['Player_Baseline'] = baselines.get(pos, 0.001)
                    pos_df['VOA'] = pos_df['Total_Points'] - pos_df['Player_Baseline']
                    top_10_voa = pos_df.sort_values('VOA', ascending=False)['VOA'].head(10).tolist()
                    top_10_voa += [None] * (10 - len(top_10_voa)) # Pad if fewer than 10 remain
                    cliff_data[pos] = top_10_voa
                
                cliff_df = pd.DataFrame(cliff_data, index=range(1, 11))
                
                fig_cliff = px.line(cliff_df, markers=True, 
                                    title=f"Top 10 Available - VOA Drop-off by Position",
                                    labels={"index": "Best Available Rank (1st to 10th)", "value": "Value Over Average (VOA)", "variable": "Position"})
                st.plotly_chart(fig_cliff, use_container_width=True)
            
            k_col = 'K' if 'K' in df.columns else 'SO'
            display_cols = ['Name', 'Injury_Status', 'Team', 'Pos', 'VOA', 'RPV', 'R', 'TB', 'RBI', 'BB', k_col, 'SB', 'Total_Points', 'Weekly_Avg']
            st.dataframe(df[display_cols].sort_values(by="VOA", ascending=False),
                         column_config={"VOA": st.column_config.NumberColumn("VOA (+/-)", format="%.1f"), "RPV": st.column_config.NumberColumn("RPV", format="%.1f%%")},
                         hide_index=True)
                         
    else:
        df = st.session_state.pitchers[st.session_state.pitchers['Drafted_By'] == "Available"].copy()
        if 'Pos' in df.columns:
            # Determine Primary Position
            df['Primary_Pos'] = df['Pos'].astype(str).str.split('/').str[0]
            df['Primary_Pos'] = df['Primary_Pos'].replace('P', 'SP') # Catch edge cases
            df['Player_Baseline'] = df['Primary_Pos'].map(baselines).fillna(baselines.get('SP', 0.001))
            
            # Calculate True VOA and RPV
            df['VOA'] = df['Total_Points'] - df['Player_Baseline']
            df['RPV'] = (df['VOA'] / df['Player_Baseline']) * 100

            positions = ["All", "SP", "RP"]
            col1, col2 = st.columns([1, 4])
            with col1:
                pos_filter = st.selectbox("Filter Pitcher Position", positions)
            
            if pos_filter != "All":
                 df = df[df['Pos'].astype(str).str.contains(pos_filter, na=False)] 
            
            with st.expander("📊 View True Value Scatter Plot (Top 100)", expanded=False):
                scatter_df = df.sort_values('VOA', ascending=False).head(100)
                fig_scatter = px.scatter(scatter_df, x="Total_Points", y="RPV", color="Primary_Pos", hover_name="Name", 
                                         title=f"Total Points vs. RPV% (Available {pos_filter} Pitchers)",
                                         labels={"Total_Points": "Total Projected Points", "RPV": "RPV (%)", "Primary_Pos": "Primary Position"})
                fig_scatter.update_layout(height=400)
                st.plotly_chart(fig_scatter, use_container_width=True)
            
            with st.expander(f"📉 View Bird's-Eye Positional Scarcity Cliff", expanded=True):
                cliff_data = {}
                target_positions = ['SP', 'RP'] if pos_filter == "All" else [pos_filter]
                
                for pos in target_positions:
                    pos_df = st.session_state.pitchers[(st.session_state.pitchers['Drafted_By'] == "Available") & (st.session_state.pitchers['Pos'].astype(str).str.contains(pos, na=False))].copy()
                    pos_df['Player_Baseline'] = baselines.get(pos, 0.001)
                    pos_df['VOA'] = pos_df['Total_Points'] - pos_df['Player_Baseline']
                    top_10_voa = pos_df.sort_values('VOA', ascending=False)['VOA'].head(10).tolist()
                    top_10_voa += [None] * (10 - len(top_10_voa))
                    cliff_data[pos] = top_10_voa
                
                cliff_df = pd.DataFrame(cliff_data, index=range(1, 11))
                
                fig_cliff = px.line(cliff_df, markers=True, 
                                    title=f"Top 10 Available - VOA Drop-off by Position",
                                    labels={"index": "Best Available Rank (1st to 10th)", "value": "Value Over Average (VOA)", "variable": "Position"})
                st.plotly_chart(fig_cliff, use_container_width=True)

            k_col = 'K' if 'K' in df.columns else 'SO'
            display_cols = ['Name', 'Injury_Status', 'Team', 'Pos', 'VOA', 'RPV', 'IP', 'H', 'ER', 'BB', k_col, 'QS', 'W', 'L', 'SV', 'HLD', 'Total_Points', 'Weekly_Avg']
            display_cols = [col for col in display_cols if col in df.columns]
            st.dataframe(df[display_cols].sort_values(by="VOA", ascending=False),
                         column_config={"VOA": st.column_config.NumberColumn("VOA (+/-)", format="%.1f"), "RPV": st.column_config.NumberColumn("RPV", format="%.1f%%")},
                         hide_index=True)

with tab2:
    st.header("Team Summaries & Analysis")
    team_view = st.selectbox("Select Team to View", st.session_state.teams)
    
    team_batters = drafted_batters[drafted_batters['Drafted_By'] == team_view].copy()
    team_pitchers = drafted_pitchers[drafted_pitchers['Drafted_By'] == team_view].copy()
    
    # Feature 3: Calculate Starting Points vs Bench Points
    team_all = pd.concat([team_batters, team_pitchers])
    starting_pts = team_all[team_all['Roster_Status'] == 'Starter']['Total_Points'].sum() if not team_all.empty else 0
    bench_pts = team_all[team_all['Roster_Status'] == 'Bench']['Total_Points'].sum() if not team_all.empty else 0
    total_proj_points = starting_pts + bench_pts
    
    col1, col2, col3 = st.columns(3)
    col1.metric("Starting Lineup Points", f"{starting_pts:.2f}")
    col2.metric("Bench Points", f"{bench_pts:.2f}")
    col3.metric("Total Roster Points", f"{total_proj_points:.2f}")
    
    # Feature 2: Edit Positions UI
    with st.expander(f"⚙️ Edit {team_view} Player Positions"):
        if not team_all.empty:
            edit_player = st.selectbox("Select Player to Edit", team_all['Name'].tolist())
            if edit_player:
                player_row = team_all[team_all['Name'] == edit_player].iloc[0]
                current_pos = player_row['Drafted_Pos']
                # Get raw eligible positions, add UTIL for batters, and P for pitchers
                eligible_pos = str(player_row['Pos']).split('/')
                if player_row.get('type', 'Batter') == 'Batter' and 'UTIL' not in eligible_pos:
                    eligible_pos.append('UTIL')
                elif player_row.get('type', 'Batter') == 'Pitcher' and 'P' not in eligible_pos:
                    eligible_pos.append('P') # <-- NEW: Allow P in the editor
                
                # Ensure the current position is in the list just in case
                pos_options = list(set(eligible_pos + [current_pos]))
                new_pos = st.selectbox("Assign New Position", pos_options, index=pos_options.index(current_pos))
                
                if st.button("Update Position", type="primary"):
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("UPDATE draft_picks SET Position = %s WHERE Name = %s AND Team = %s", (new_pos, edit_player, team_view))
                    conn.commit()
                    conn.close()
                    st.success(f"Updated {edit_player} to {new_pos}!")
                    st.rerun()

    st.markdown("---")

    # Display tables with Eligible Positions (Pos) and Starter/Bench Status
    st.subheader("Hitters")
    hitter_display = ['Name', 'Roster_Status', 'Drafted_Pos', 'Pos', 'Total_Points', 'Weekly_Avg'] 
    st.dataframe(team_batters[hitter_display].sort_values(by=['Roster_Status', 'Total_Points'], ascending=[False, False]), hide_index=True, use_container_width=True)
    
    st.subheader("Pitchers")
    pitcher_display = ['Name', 'Roster_Status', 'Drafted_Pos', 'Pos', 'Total_Points', 'Weekly_Avg'] 
    st.dataframe(team_pitchers[pitcher_display].sort_values(by=['Roster_Status', 'Total_Points'], ascending=[False, False]), hide_index=True, use_container_width=True)

with tab3:
    st.header("Live League Standings")
    
    if drafted_batters.empty and drafted_pitchers.empty:
        st.info("No players have been drafted yet.")
    else:
        st.subheader("Roster Construction X-Ray")
        dbat = drafted_batters[['Drafted_By', 'Drafted_Pos', 'Total_Points']].copy()
        dpit = drafted_pitchers[['Drafted_By', 'Drafted_Pos', 'Total_Points']].copy()
        alloc_df = pd.concat([dbat, dpit])
        
        alloc_grouped = alloc_df.groupby(['Drafted_By', 'Drafted_Pos'])['Total_Points'].sum().reset_index()

        # Feature 1: Custom Sorting Order for the X-Ray Chart
        custom_order = ['SP', 'RP', 'P', 'C', '1B', '2B', 'SS', '3B', 'OF', 'UTIL', 'UTIL/SP']
        
        fig_alloc = px.bar(alloc_grouped, y='Drafted_By', x='Total_Points', color='Drafted_Pos', 
                           orientation='h', title="Projected Points by Position per Team",
                           labels={'Drafted_By': 'Team', 'Total_Points': 'Total Projected Points'},
                           category_orders={'Drafted_Pos': custom_order}) # Apply custom sorting
                           
        fig_alloc.update_layout(barmode='stack', yaxis={'categoryorder':'total ascending'}, height=500)
        st.plotly_chart(fig_alloc, use_container_width=True)
        st.markdown("---")

        # Feature 3: Calculate True Standings (Starters Only)
        combined_drafted = pd.concat([drafted_batters, drafted_pitchers])
        
        # 1. Sum up Starting Points
        starters_df = combined_drafted[combined_drafted['Roster_Status'] == 'Starter']
        standings = starters_df.groupby('Drafted_By')[['Total_Points', 'Weekly_Avg']].sum().reset_index()
        standings = standings.rename(columns={'Drafted_By': 'Team', 'Total_Points': 'Starting Proj Points', 'Weekly_Avg': 'Starting Weekly Avg'})
        
        # 2. Sum up Bench Points separately
        bench_df = combined_drafted[combined_drafted['Roster_Status'] == 'Bench']
        if not bench_df.empty:
            bench_pts = bench_df.groupby('Drafted_By')['Total_Points'].sum().reset_index()
            bench_pts = bench_pts.rename(columns={'Drafted_By': 'Team', 'Total_Points': 'Bench Points'})
            # Merge them together
            standings = pd.merge(standings, bench_pts, on='Team', how='left').fillna(0)
        else:
            standings['Bench Points'] = 0.0

        st.subheader("True League Standings (Optimized Starting Lineups)")
        st.dataframe(
            standings.sort_values(by='Starting Proj Points', ascending=False),
            column_config={
                "Starting Proj Points": st.column_config.NumberColumn(format="%.2f"), 
                "Starting Weekly Avg": st.column_config.NumberColumn(format="%.2f"),
                "Bench Points": st.column_config.NumberColumn(format="%.2f")
            },
            hide_index=True, use_container_width=True 
        )

with tab4:
    st.header("Draft Board")
    conn = get_db_connection()
    draft_df = pd.read_sql_query("SELECT * FROM draft_picks", conn)
    conn.close()
    
    if not draft_df.empty:
        board = pd.DataFrame(index=range(1, 22), columns=st.session_state.teams)
        
        for team in st.session_state.teams:
            # Using Postgres lowercase column names mapped out of Pandas
            team_picks = draft_df[draft_df['team'] == team]
            for i, (_, row) in enumerate(team_picks.iterrows()):
                board.at[i+1, team] = f"{row['name']} ({row['position']})"
        
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