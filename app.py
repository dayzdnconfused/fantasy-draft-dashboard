import streamlit as st
import pandas as pd
import os

# --- PHASE 1: BASELINE ENGINE ---
def calculate_baselines(batters, pitchers, pools):
    """Calculates baselines against the full player universe so they remain static during the draft."""
    baselines = {}
    used_batter_names = set()
    
    # 1. Offensive Baselines (Allows multi-position double counting)
    if 'Pos' in batters.columns:
        for pos in ['C', '1B', '2B', '3B', 'SS', 'OF']:
            # Find all players eligible for this position
            eligible = batters[batters['Pos'].astype(str).str.contains(pos, na=False)]
            # Take the top N players based on the slider settings
            top_n = eligible.nlargest(pools[pos], 'Total_Points')
            
            if not top_n.empty:
                baselines[pos] = top_n['Total_Points'].mean()
                used_batter_names.update(top_n['Name'].tolist())
            else:
                baselines[pos] = 0.001 # Failsafe to prevent division by zero

        # 2. UTIL Baseline (The true leftovers)
        # Filter out anyone who was used in a starting positional pool
        remaining_batters = batters[~batters['Name'].isin(used_batter_names)]
        top_util = remaining_batters.nlargest(20, 'Total_Points')
        baselines['UTIL'] = top_util['Total_Points'].mean() if not top_util.empty else 0.001

    # 3. Pitching Baselines
    if 'Pos' in pitchers.columns:
        for pos in ['SP', 'RP']:
            eligible = pitchers[pitchers['Pos'].astype(str).str.contains(pos, na=False)]
            top_n = eligible.nlargest(pools[pos], 'Total_Points')
            baselines[pos] = top_n['Total_Points'].mean() if not top_n.empty else 0.001
            
    return baselines

# --- PHASE 2: DATA SETUP ---
@st.cache_data
def load_data():
    batters = pd.read_csv("the_bat_x_batters.csv") 
    pitchers = pd.read_csv("atc_pitchers.csv")
    
    # Load the master ID map
    id_map = pd.read_csv("id_map.csv")
    
    # --- SCHEMA ALIGNMENT ---
    # Rename IDFANGRAPHS to PlayerId, and use the robust ALLPOS column for positions
    id_map = id_map.rename(columns={
        'IDFANGRAPHS': 'PlayerId', 
        'ALLPOS': 'Pos' 
    })
    
    # 1. Clean the data: Force all text to uppercase (e.g., "1b/2b/ss" becomes "1B/2B/SS")
    id_map['Pos'] = id_map['Pos'].str.upper()
    
    # 2. Apply Rule: Convert any generic 'P' into an 'SP'
    # The r'\bP\b' tells Python to only look for 'P' when it is a standalone word, 
    # preventing it from accidentally changing 'RP' into 'RSP'.
    id_map['Pos'] = id_map['Pos'].str.replace(r'\bP\b', 'SP', regex=True)
    
    # Merge the cleaned 'Pos' column into the batters dataframe using 'PlayerId'
    batters = pd.merge(
        batters, 
        id_map[['PlayerId', 'Pos']], 
        on='PlayerId', 
        how='left' 
    )

    # Merge the cleaned 'Pos' column into the pitchers dataframe using 'PlayerId'
    pitchers = pd.merge(
        pitchers, 
        id_map[['PlayerId', 'Pos']], 
        on='PlayerId', 
        how='left' 
    )

    # Calculate Total Bases (TB)
    if all(col in batters.columns for col in ['H', '2B', '3B', 'HR']):
        batters['TB'] = batters['H'] + batters['2B'] + (2 * batters['3B']) + (3 * batters['HR'])
    
    # Apply Custom Batter Scoring
    batters['Total_Points'] = (
        batters['R'] * 1 + batters.get('TB', 0) * 1 + batters['RBI'] * 1 +
        batters['BB'] * 1 + batters['SO'] * -1 + batters['SB'] * 1
    )
    
    # Apply Custom Pitcher Scoring
    pitchers['Total_Points'] = (
        pitchers['IP'] * 3 + pitchers['H'] * -1 + pitchers['ER'] * -2 +
        pitchers['BB'] * -1 + pitchers['SO'] * 1 + pitchers.get('QS', 0) * 1 +
        pitchers['W'] * 2 + pitchers['L'] * -2 + pitchers.get('SV', 0) * 5 + pitchers.get('HLD', 0) * 2
    )

    # Calculate Weekly Averages (24 weeks)
    batters['Weekly_Avg'] = batters['Total_Points'] / 24
    pitchers['Weekly_Avg'] = pitchers['Total_Points'] / 24
    
    batters['Drafted_By'] = "Available"
    pitchers['Drafted_By'] = "Available"
    
    # State Persistence Logic
    if os.path.exists("draft_state.csv"):
        state_df = pd.read_csv("draft_state.csv")
        for index, row in state_df.iterrows():
            if row['Type'] == 'Batter':
                batters.loc[batters['Name'] == row['Name'], 'Drafted_By'] = row['Team']
            else:
                pitchers.loc[pitchers['Name'] == row['Name'], 'Drafted_By'] = row['Team']
                
    return batters, pitchers

# --- PHASE 3: BUILDING THE UI ---
st.set_page_config(layout="wide")
st.title("2026 Fantasy Baseball Draft Room")

if 'batters' not in st.session_state:
    st.session_state.batters, st.session_state.pitchers = load_data()

# --- SIDEBAR: DRAFT CONTROLS ---
st.sidebar.header("Draft a Player")
player_type = st.sidebar.radio("Player Type", ["Batter", "Pitcher"])
teams = [f"Team {i}" for i in range(1, 11)]
selected_team = st.sidebar.selectbox("Selecting Team", teams)

if player_type == "Batter":
    available_players = st.session_state.batters[st.session_state.batters['Drafted_By'] == "Available"]['Name'].tolist()
else:
    available_players = st.session_state.pitchers[st.session_state.pitchers['Drafted_By'] == "Available"]['Name'].tolist()

selected_player = st.sidebar.selectbox("Player", available_players)

if st.sidebar.button("Draft Player"):
    if player_type == "Batter":
        st.session_state.batters.loc[st.session_state.batters['Name'] == selected_player, 'Drafted_By'] = selected_team
    else:
        st.session_state.pitchers.loc[st.session_state.pitchers['Name'] == selected_player, 'Drafted_By'] = selected_team
        
    new_draft_record = pd.DataFrame({'Name': [selected_player], 'Type': [player_type], 'Team': [selected_team]})
    if os.path.exists("draft_state.csv"):
        new_draft_record.to_csv("draft_state.csv", mode='a', header=False, index=False)
    else:
        new_draft_record.to_csv("draft_state.csv", mode='w', header=True, index=False)
        
    st.sidebar.success(f"{selected_player} drafted by {selected_team}")

st.sidebar.markdown("---")
st.sidebar.header("Data Controls")

if st.sidebar.button("Refresh Projections & Cache"):
    st.cache_data.clear()
    st.session_state.batters, st.session_state.pitchers = load_data()
    st.sidebar.success("Projections reloaded from CSV and draft state restored!")

# --- NEW: POOL SETTINGS & BASELINE GENERATION ---
st.sidebar.markdown("---")
st.sidebar.header("RPV Pool Settings")
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

# Generate the baselines silently in the background using the FULL dataset
baselines = calculate_baselines(st.session_state.batters, st.session_state.pitchers, current_pools)


# --- MAIN DASHBOARD ---
tab1, tab2, tab3 = st.tabs(["Available Players", "Team Rosters", "League Standings"])

with tab1:
    st.header("Available Projections")
    view_type = st.radio("View", ["Batters", "Pitchers"], horizontal=True)
    
    if view_type == "Batters":
        df = st.session_state.batters[st.session_state.batters['Drafted_By'] == "Available"].copy()
        
        if 'Pos' in df.columns:
            positions = ["All", "C", "1B", "2B", "3B", "SS", "OF", "UTIL"]
            
            # --- UX FIX: Constrain the dropdown width ---
            col1, col2 = st.columns([1, 4]) # Creates a small left column and a large empty right column
            with col1:
                pos_filter = st.selectbox("Filter Position", positions)
            
            if pos_filter != "All" and pos_filter != "UTIL":
                df = df[df['Pos'].astype(str).str.contains(pos_filter, na=False)]
                
            active_baseline = baselines.get('UTIL', 0.001) if pos_filter == "All" else baselines.get(pos_filter, 0.001)
                
            df['VOA'] = df['Total_Points'] - active_baseline
            # --- MATH FIX: Multiply by 100 for percentage ---
            df['RPV'] = (df['VOA'] / active_baseline) * 100 
            
            display_cols = ['Name', 'Team', 'Pos', 'VOA', 'RPV', 'R', 'TB', 'RBI', 'BB', 'SO', 'SB', 'Total_Points', 'Weekly_Avg']
            
            st.dataframe(
                df[display_cols].sort_values(by="VOA", ascending=False),
                column_config={
                    "VOA": st.column_config.NumberColumn("VOA (+/-)", format="%.1f"),
                    # --- FORMAT FIX: Add the % symbol ---
                    "RPV": st.column_config.NumberColumn("RPV", format="%.1f%%") 
                },
                hide_index=True
            )

        else:
            st.warning("No 'Pos' column found in batter data.")
            
    else:
        df = st.session_state.pitchers[st.session_state.pitchers['Drafted_By'] == "Available"].copy()
        
        if 'Pos' in df.columns:
            positions = ["All", "SP", "RP"]
            
            # --- UX FIX ---
            col1, col2 = st.columns([1, 4])
            with col1:
                pos_filter = st.selectbox("Filter Pitcher Position", positions)
            
            if pos_filter != "All":
                 df = df[df['Pos'].astype(str).str.contains(pos_filter, na=False)] 
            
            active_baseline = baselines.get('SP', 0.001) if pos_filter == "All" else baselines.get(pos_filter, 0.001)
            
            df['VOA'] = df['Total_Points'] - active_baseline
            # --- MATH FIX ---
            df['RPV'] = (df['VOA'] / active_baseline) * 100
                 
            display_cols = ['Name', 'Team', 'Pos', 'VOA', 'RPV', 'IP', 'H', 'ER', 'BB', 'SO', 'QS', 'W', 'L', 'SV', 'HLD', 'Total_Points', 'Weekly_Avg']
            
            display_cols = [col for col in display_cols if col in df.columns]
            st.dataframe(
                df[display_cols].sort_values(by="VOA", ascending=False),
                column_config={
                    "VOA": st.column_config.NumberColumn("VOA (+/-)", format="%.1f"),
                    # --- FORMAT FIX ---
                    "RPV": st.column_config.NumberColumn("RPV", format="%.1f%%") 
                },
                hide_index=True
            )

        else:
             st.warning("No 'Pos' column found in pitcher data.")

with tab2:
    st.header("Team Summaries")
    team_view = st.selectbox("Select Team to View", teams)
    
    team_batters = st.session_state.batters[st.session_state.batters['Drafted_By'] == team_view]
    team_pitchers = st.session_state.pitchers[st.session_state.pitchers['Drafted_By'] == team_view]
    
    total_proj_points = team_batters['Total_Points'].sum() + team_pitchers['Total_Points'].sum()
    total_weekly_avg = team_batters['Weekly_Avg'].sum() + team_pitchers['Weekly_Avg'].sum()
    
    col1, col2 = st.columns(2)
    col1.metric("Projected Total Points", f"{total_proj_points:.2f}")
    col2.metric("Projected Weekly Average", f"{total_weekly_avg:.2f}")
    
    st.subheader("Hitters")
    hitter_display = ['Name', 'Pos', 'Total_Points', 'Weekly_Avg'] if 'Pos' in team_batters.columns else ['Name', 'Total_Points', 'Weekly_Avg']
    st.dataframe(team_batters[hitter_display], hide_index=True)
    
    st.subheader("Pitchers")
    st.dataframe(team_pitchers[['Name', 'Total_Points', 'Weekly_Avg']], hide_index=True)

with tab3:
    st.header("Live League Standings")
    
    drafted_batters = st.session_state.batters[st.session_state.batters['Drafted_By'] != "Available"]
    drafted_pitchers = st.session_state.pitchers[st.session_state.pitchers['Drafted_By'] != "Available"]
    
    if drafted_batters.empty and drafted_pitchers.empty:
        st.info("No players have been drafted yet. Standings will appear here once the draft begins.")
    else:
        b_standings = pd.DataFrame()
        if not drafted_batters.empty:
            b_standings = drafted_batters.groupby('Drafted_By')[['Total_Points', 'Weekly_Avg']].sum().reset_index()
            
        p_standings = pd.DataFrame()
        if not drafted_pitchers.empty:
            p_standings = drafted_pitchers.groupby('Drafted_By')[['Total_Points', 'Weekly_Avg']].sum().reset_index()
            
        combined = pd.concat([b_standings, p_standings])
        league_standings = combined.groupby('Drafted_By')[['Total_Points', 'Weekly_Avg']].sum().reset_index()
        
        league_standings = league_standings.rename(columns={
            'Drafted_By': 'Team', 
            'Total_Points': 'Proj Total Points', 
            'Weekly_Avg': 'Proj Weekly Avg'
        })
        league_standings = league_standings.sort_values(by='Proj Total Points', ascending=False)
        
        st.dataframe(
            league_standings,
            column_config={
                "Proj Total Points": st.column_config.NumberColumn(format="%.2f"),
                "Proj Weekly Avg": st.column_config.NumberColumn(format="%.2f")
            },
            hide_index=True,
            use_container_width=True 
        )