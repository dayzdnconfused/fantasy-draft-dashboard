import streamlit as st
import pandas as pd
import os

# --- PHASE 2: DATA SETUP ---
@st.cache_data
@st.cache_data
def load_data():
    batters = pd.read_csv("the_bat_x_batters.csv") 
    pitchers = pd.read_csv("atc_pitchers.csv")
    
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
    
    # --- NEW: STATE PERSISTENCE LOGIC ---
    # If a save file exists, read it and re-apply the drafted statuses
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

# Initialize session state to keep track of drafted players across app refreshes
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

# The Draft Button
if st.sidebar.button("Draft Player"):
    # 1. Update the live session state
    if player_type == "Batter":
        st.session_state.batters.loc[st.session_state.batters['Name'] == selected_player, 'Drafted_By'] = selected_team
    else:
        st.session_state.pitchers.loc[st.session_state.pitchers['Name'] == selected_player, 'Drafted_By'] = selected_team
        
    # 2. Write to the persistent save file
    new_draft_record = pd.DataFrame({'Name': [selected_player], 'Type': [player_type], 'Team': [selected_team]})
    if os.path.exists("draft_state.csv"):
        new_draft_record.to_csv("draft_state.csv", mode='a', header=False, index=False)
    else:
        new_draft_record.to_csv("draft_state.csv", mode='w', header=True, index=False)
        
    st.sidebar.success(f"{selected_player} drafted by {selected_team}")

st.sidebar.markdown("---")
st.sidebar.header("Data Controls")

# The Refresh Button
if st.sidebar.button("Refresh Projections & Cache"):
    st.cache_data.clear()
    st.session_state.batters, st.session_state.pitchers = load_data()
    st.sidebar.success("Projections reloaded from CSV and draft state restored!")

# --- MAIN DASHBOARD ---
tab1, tab2, tab3 = st.tabs(["Available Players", "Team Rosters", "League Standings"])

with tab1:
    st.header("Available Projections")
    view_type = st.radio("View", ["Batters", "Pitchers"], horizontal=True)
    
    if view_type == "Batters":
        df = st.session_state.batters[st.session_state.batters['Drafted_By'] == "Available"]
        
        # Safe check for Position column (FanGraphs usually uses 'Pos' or 'position')
        if 'Pos' in df.columns:
            positions = df['Pos'].unique().tolist()
            pos_filter = st.selectbox("Filter Position", ["All"] + positions)
            if pos_filter != "All":
                df = df[df['Pos'] == pos_filter]
            display_cols = ['Name', 'Team', 'Pos', 'R', 'TB', 'RBI', 'BB', 'SO', 'SB', 'Total_Points', 'Weekly_Avg']
        else:
            display_cols = ['Name', 'Team', 'R', 'TB', 'RBI', 'BB', 'SO', 'SB', 'Total_Points', 'Weekly_Avg']
            
        st.dataframe(df[display_cols].sort_values(by="Total_Points", ascending=False), hide_index=True)
        
    else:
        df = st.session_state.pitchers[st.session_state.pitchers['Drafted_By'] == "Available"]
        
        # FanGraphs pitching usually has a 'Role' or 'Pos' column for SP/RP
        if 'Role' in df.columns:
            role_filter = st.selectbox("Filter Role", ["All"] + df['Role'].unique().tolist())
            if role_filter != "All":
                 df = df[df['Role'] == role_filter] 
                 
        display_cols = ['Name', 'Team', 'IP', 'H', 'ER', 'BB', 'SO', 'QS', 'W', 'L', 'SV', 'HLD', 'Total_Points', 'Weekly_Avg']
        
        # Only display columns that actually exist in the CSV to prevent errors
        display_cols = [col for col in display_cols if col in df.columns]
        st.dataframe(df[display_cols].sort_values(by="Total_Points", ascending=False), hide_index=True)

with tab2:
    st.header("Team Summaries")
    team_view = st.selectbox("Select Team to View", teams)
    
    # Filter drafted players for the selected team
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
    
    # 1. Filter out the available players, leaving only the drafted ones
    drafted_batters = st.session_state.batters[st.session_state.batters['Drafted_By'] != "Available"]
    drafted_pitchers = st.session_state.pitchers[st.session_state.pitchers['Drafted_By'] != "Available"]
    
    if drafted_batters.empty and drafted_pitchers.empty:
        st.info("No players have been drafted yet. Standings will appear here once the draft begins.")
    else:
        # 2. Group batters and pitchers by Team and sum their points
        b_standings = pd.DataFrame()
        if not drafted_batters.empty:
            b_standings = drafted_batters.groupby('Drafted_By')[['Total_Points', 'Weekly_Avg']].sum().reset_index()
            
        p_standings = pd.DataFrame()
        if not drafted_pitchers.empty:
            p_standings = drafted_pitchers.groupby('Drafted_By')[['Total_Points', 'Weekly_Avg']].sum().reset_index()
            
        # 3. Combine both groups and do one final sum to get total team points
        combined = pd.concat([b_standings, p_standings])
        league_standings = combined.groupby('Drafted_By')[['Total_Points', 'Weekly_Avg']].sum().reset_index()
        
        # 4. Clean up the table for display
        league_standings = league_standings.rename(columns={
            'Drafted_By': 'Team', 
            'Total_Points': 'Proj Total Points', 
            'Weekly_Avg': 'Proj Weekly Avg'
        })
        league_standings = league_standings.sort_values(by='Proj Total Points', ascending=False)
        
        # 5. Render the leaderboard
        st.dataframe(
            league_standings,
            column_config={
                "Proj Total Points": st.column_config.NumberColumn(format="%.2f"),
                "Proj Weekly Avg": st.column_config.NumberColumn(format="%.2f")
            },
            hide_index=True,
            use_container_width=True # Stretches the table to look like a proper leaderboard
        )