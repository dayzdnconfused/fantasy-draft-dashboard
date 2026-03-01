import streamlit as st
import pandas as pd
import os

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

# --- PHASE 2: DATA SETUP & TEAM LOGIC ---
def load_teams():
    """Loads custom team names or generates defaults."""
    if os.path.exists("teams.csv"):
        return pd.read_csv("teams.csv")['Team'].tolist()
    return [f"Team {i}" for i in range(1, 11)]

@st.cache_data
def load_data():
    batters = pd.read_csv("the_bat_x_batters.csv") 
    pitchers = pd.read_csv("atc_pitchers.csv")
    id_map = pd.read_csv("id_map.csv")
    
    # --- PHASE 2: DATA SETUP & TEAM LOGIC ---

    id_map = pd.read_csv("id_map.csv")
    
    id_map = id_map.rename(columns={'IDFANGRAPHS': 'PlayerId', 'ALLPOS': 'Pos'})
    id_map['Pos'] = id_map['Pos'].str.upper()
    id_map['Pos'] = id_map['Pos'].str.replace(r'\bP\b', 'SP', regex=True)
    
    # --- BUG 1 FIX: Drop duplicate IDs so players like Shohei don't multiply ---
    id_map_clean = id_map[['PlayerId', 'Pos']].drop_duplicates(subset=['PlayerId'])
    
    batters = pd.merge(batters, id_map_clean, on='PlayerId', how='left')
    pitchers = pd.merge(pitchers, id_map_clean, on='PlayerId', how='left')

    if all(col in batters.columns for col in ['H', '2B', '3B', 'HR']):
        batters['TB'] = batters['H'] + batters['2B'] + (2 * batters['3B']) + (3 * batters['HR'])
    
    batters['Total_Points'] = (
        batters['R'] * 1 + batters.get('TB', 0) * 1 + batters['RBI'] * 1 +
        batters['BB'] * 1 + batters['SO'] * -1 + batters['SB'] * 1
    )
    
    pitchers['Total_Points'] = (
        pitchers['IP'] * 3 + pitchers['H'] * -1 + pitchers['ER'] * -2 +
        pitchers['BB'] * -1 + pitchers['SO'] * 1 + pitchers.get('QS', 0) * 1 +
        pitchers['W'] * 2 + pitchers['L'] * -2 + pitchers.get('SV', 0) * 5 + pitchers.get('HLD', 0) * 2
    )

    batters['Weekly_Avg'] = batters['Total_Points'] / 24
    pitchers['Weekly_Avg'] = pitchers['Total_Points'] / 24
    
    batters['Drafted_By'] = "Available"
    pitchers['Drafted_By'] = "Available"
    batters['Drafted_Pos'] = None
    pitchers['Drafted_Pos'] = None
    
    if os.path.exists("draft_state.csv"):
        state_df = pd.read_csv("draft_state.csv")
        # Safety check for old save files that don't have the Position column yet
        if 'Position' not in state_df.columns:
            state_df['Position'] = 'UTIL' 
            
        for index, row in state_df.iterrows():
            if row['Type'] == 'Batter':
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

# --- SNAKE DRAFT CALCULATOR ---
# Calculate exactly where we are in the draft to auto-assign the next team
total_drafted = 0
if os.path.exists("draft_state.csv"):
    total_drafted = len(pd.read_csv("draft_state.csv"))

num_teams = len(st.session_state.teams)
current_round = (total_drafted // num_teams) + 1
pick_in_round = total_drafted % num_teams

# Odd rounds go 0-9, Even rounds go 9-0 (Snake Logic)
if current_round % 2 != 0:
    team_on_clock_idx = pick_in_round
else:
    team_on_clock_idx = (num_teams - 1) - pick_in_round

# --- SIDEBAR: DRAFT CONTROLS ---
st.sidebar.header(f"Draft Room - Pick {total_drafted + 1}")
st.sidebar.markdown(f"**Round {current_round} | On the Clock:**")

# We use the calculated index to default the dropdown to the correct team
selected_team = st.sidebar.selectbox("Selecting Team", st.session_state.teams, index=team_on_clock_idx)

player_type = st.sidebar.radio("Player Type", ["Batter", "Pitcher"], horizontal=True)

if player_type == "Batter":
    available_players = st.session_state.batters[st.session_state.batters['Drafted_By'] == "Available"]['Name'].tolist()
else:
    available_players = st.session_state.pitchers[st.session_state.pitchers['Drafted_By'] == "Available"]['Name'].tolist()

selected_player = st.sidebar.selectbox("Player", available_players)

# Dynamically parse the player's position column to create a dropdown for how they will be slotted
if selected_player:
    if player_type == "Batter":
        raw_pos = st.session_state.batters.loc[st.session_state.batters['Name'] == selected_player, 'Pos'].values[0]
        pos_options = str(raw_pos).split('/') + ['UTIL']
    else:
        raw_pos = st.session_state.pitchers.loc[st.session_state.pitchers['Name'] == selected_player, 'Pos'].values[0]
        pos_options = str(raw_pos).split('/')
        
    selected_pos = st.sidebar.selectbox("Draft As (Position)", pos_options)

if st.sidebar.button("Draft Player", type="primary"):
    # --- THE OHTANI EXCEPTION (DRAFT) ---
    if selected_player == "Shohei Ohtani":
        # Draft him in both dataframes simultaneously
        st.session_state.batters.loc[st.session_state.batters['Name'] == selected_player, 'Drafted_By'] = selected_team
        st.session_state.batters.loc[st.session_state.batters['Name'] == selected_player, 'Drafted_Pos'] = "UTIL"
        st.session_state.pitchers.loc[st.session_state.pitchers['Name'] == selected_player, 'Drafted_By'] = selected_team
        st.session_state.pitchers.loc[st.session_state.pitchers['Name'] == selected_player, 'Drafted_Pos'] = "SP"
        
        # Override the save variables so he takes up 1 row, but is marked as Two-Way
        record_type = "Two-Way"
        record_pos = "UTIL/SP"
    else:
        # Standard Draft Logic
        if player_type == "Batter":
            st.session_state.batters.loc[st.session_state.batters['Name'] == selected_player, 'Drafted_By'] = selected_team
            st.session_state.batters.loc[st.session_state.batters['Name'] == selected_player, 'Drafted_Pos'] = selected_pos
        else:
            st.session_state.pitchers.loc[st.session_state.pitchers['Name'] == selected_player, 'Drafted_By'] = selected_team
            st.session_state.pitchers.loc[st.session_state.pitchers['Name'] == selected_player, 'Drafted_Pos'] = selected_pos
            
        record_type = player_type
        record_pos = selected_pos
        
    # Safely reconstruct the CSV
    new_record = pd.DataFrame({'Name': [selected_player], 'Type': [record_type], 'Team': [selected_team], 'Position': [record_pos]})
    
    if os.path.exists("draft_state.csv"):
        try:
            draft_df = pd.read_csv("draft_state.csv")
            draft_df = pd.concat([draft_df, new_record], ignore_index=True)
            draft_df.to_csv("draft_state.csv", index=False)
        except Exception:
            new_record.to_csv("draft_state.csv", index=False)
    else:
        new_record.to_csv("draft_state.csv", index=False)
        
    st.sidebar.success(f"{selected_player} drafted to {selected_team} as {record_pos}")
    st.rerun()
        
    # --- BUG 2 FIX: Safely reconstruct the CSV to prevent column shifting ---
    new_record = pd.DataFrame({'Name': [selected_player], 'Type': [player_type], 'Team': [selected_team], 'Position': [selected_pos]})
    
    if os.path.exists("draft_state.csv"):
        state_df = pd.read_csv("draft_state.csv")
        if 'Position' not in state_df.columns:
            state_df['Position'] = 'UTIL' 
            
        for index, row in state_df.iterrows():
            # --- THE OHTANI EXCEPTION (LOAD) ---
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
        
    st.sidebar.success(f"{selected_player} drafted to {selected_team} as {selected_pos}")
    st.rerun() # Instantly refreshes the board and advances the snake draft counter

st.sidebar.markdown("---")
st.sidebar.header("League Settings")
with st.sidebar.expander("Customize Team Names"):
    st.warning("Only edit before the draft begins!")
    new_teams = []
    for i, default_name in enumerate(st.session_state.teams):
        new_teams.append(st.text_input(f"Team {i+1}", value=default_name))
        
    if st.button("Save Team Names"):
        pd.DataFrame({'Team': new_teams}).to_csv("teams.csv", index=False)
        st.session_state.teams = new_teams
        st.success("Teams saved!")
        st.rerun()

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

if st.sidebar.button("Refresh Projections & Cache"):
    st.cache_data.clear()
    st.session_state.batters, st.session_state.pitchers = load_data()
    st.sidebar.success("Projections reloaded!")

baselines = calculate_baselines(st.session_state.batters, st.session_state.pitchers, current_pools)

# --- MAIN DASHBOARD ---
tab1, tab2, tab3, tab4 = st.tabs(["Available Players", "Team Rosters", "League Standings", "Draft Board"])

with tab1:
    st.header("Available Projections")
    view_type = st.radio("View", ["Batters", "Pitchers"], horizontal=True)
    
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
            display_cols = ['Name', 'Team', 'Pos', 'VOA', 'RPV', 'R', 'TB', 'RBI', 'BB', 'SO', 'SB', 'Total_Points', 'Weekly_Avg']
            st.dataframe(
                df[display_cols].sort_values(by="VOA", ascending=False),
                column_config={"VOA": st.column_config.NumberColumn("VOA (+/-)", format="%.1f"), "RPV": st.column_config.NumberColumn("RPV", format="%.1f%%")},
                hide_index=True
            )
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
            display_cols = ['Name', 'Team', 'Pos', 'VOA', 'RPV', 'IP', 'H', 'ER', 'BB', 'SO', 'QS', 'W', 'L', 'SV', 'HLD', 'Total_Points', 'Weekly_Avg']
            display_cols = [col for col in display_cols if col in df.columns]
            st.dataframe(
                df[display_cols].sort_values(by="VOA", ascending=False),
                column_config={"VOA": st.column_config.NumberColumn("VOA (+/-)", format="%.1f"), "RPV": st.column_config.NumberColumn("RPV", format="%.1f%%")},
                hide_index=True
            )

with tab2:
    st.header("Team Summaries")
    team_view = st.selectbox("Select Team to View", st.session_state.teams)
    
    team_batters = st.session_state.batters[st.session_state.batters['Drafted_By'] == team_view]
    team_pitchers = st.session_state.pitchers[st.session_state.pitchers['Drafted_By'] == team_view]
    
    total_proj_points = team_batters['Total_Points'].sum() + team_pitchers['Total_Points'].sum()
    total_weekly_avg = team_batters['Weekly_Avg'].sum() + team_pitchers['Weekly_Avg'].sum()
    
    col1, col2 = st.columns(2)
    col1.metric("Projected Total Points", f"{total_proj_points:.2f}")
    col2.metric("Projected Weekly Average", f"{total_weekly_avg:.2f}")
    
    st.subheader("Hitters")
    hitter_display = ['Name', 'Drafted_Pos', 'Total_Points', 'Weekly_Avg'] if 'Drafted_Pos' in team_batters.columns else ['Name', 'Total_Points', 'Weekly_Avg']
    st.dataframe(team_batters[hitter_display], hide_index=True)
    
    st.subheader("Pitchers")
    pitcher_display = ['Name', 'Drafted_Pos', 'Total_Points', 'Weekly_Avg'] if 'Drafted_Pos' in team_pitchers.columns else ['Name', 'Total_Points', 'Weekly_Avg']
    st.dataframe(team_pitchers[pitcher_display], hide_index=True)

with tab3:
    st.header("Live League Standings")
    drafted_batters = st.session_state.batters[st.session_state.batters['Drafted_By'] != "Available"]
    drafted_pitchers = st.session_state.pitchers[st.session_state.pitchers['Drafted_By'] != "Available"]
    
    if drafted_batters.empty and drafted_pitchers.empty:
        st.info("No players have been drafted yet.")
    else:
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
    if os.path.exists("draft_state.csv"):
        draft_df = pd.read_csv("draft_state.csv")
        if 'Position' not in draft_df.columns:
            draft_df['Position'] = 'UTIL'
            
        board = pd.DataFrame(index=range(1, 22), columns=st.session_state.teams)
        
        for team in st.session_state.teams:
            team_picks = draft_df[draft_df['Team'] == team]
            for i, (_, row) in enumerate(team_picks.iterrows()):
                # Format: Player Name (POS)
                board.at[i+1, team] = f"{row['Name']} ({row['Position']})"
        
        def color_board(val):
            if pd.isna(val) or val == "": return ''
            if 'UTIL/SP' in val: return 'background-color: #ffd700; color: black;' # Gold for Ohtani
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