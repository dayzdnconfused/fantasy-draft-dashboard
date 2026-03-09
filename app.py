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

    # --- NEW: RESTORE FROM CSV ---
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
    
    # --- EXISTING UNDO/RESET LOGIC ---
    if st.button("Undo Last Pick"):
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT id, Name FROM draft_picks ORDER BY id DESC LIMIT 1")
        last_pick = c.fetchone()
        
        if last_pick:
            row_id, player_name = last_pick
            c.execute("DELETE FROM draft_picks WHERE id = %s", (row_id,))
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
        conn = get_db_connection()
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
    
    team_batters = st.session_state.batters[st.session_state.batters['Drafted_By'] == team_view]
    team_pitchers = st.session_state.pitchers[st.session_state.pitchers['Drafted_By'] == team_view]
    
    total_proj_points = team_batters['Total_Points'].sum() + team_pitchers['Total_Points'].sum()
    total_weekly_avg = team_batters['Weekly_Avg'].sum() + team_pitchers['Weekly_Avg'].sum()
    
    col1, col2 = st.columns(2)
    col1.metric("Projected Total Points", f"{total_proj_points:.2f}")
    col2.metric("Projected Weekly Average", f"{total_weekly_avg:.2f}")
    
    st.markdown("---")
    st.subheader("Roster Balance Analysis")
    
    league_bat_pts = drafted_batters['Total_Points'].sum() if not drafted_batters.empty else 0
    league_pit_pts = drafted_pitchers['Total_Points'].sum() if not drafted_pitchers.empty else 0
    league_total = league_bat_pts + league_pit_pts
    
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
        st.subheader("Roster Construction X-Ray")
        dbat = drafted_batters[['Drafted_By', 'Drafted_Pos', 'Total_Points']].copy()
        dpit = drafted_pitchers[['Drafted_By', 'Drafted_Pos', 'Total_Points']].copy()
        alloc_df = pd.concat([dbat, dpit])
        
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