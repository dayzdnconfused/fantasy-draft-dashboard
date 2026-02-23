import streamlit as st
import pandas as pd

# --- PHASE 2: DATA SETUP ---
@st.cache_data
def load_data():
    # Load your downloaded FanGraphs projections (THE BAT X for batters, ATC for pitchers)
    batters = pd.read_csv("the_bat_x_batters.csv") 
    pitchers = pd.read_csv("atc_pitchers.csv")
    
    # --- NEW: Calculate Total Bases (TB) ---
    # TB = Hits + Doubles + (2 * Triples) + (3 * Home Runs)
    batters['TB'] = batters['H'] + batters['2B'] + (2 * batters['3B']) + (3 * batters['HR'])

    # 1. Apply Custom Batter Scoring
    # R=1, TB=1, RBI=1, BB=1, K=-1, SB=1
    batters['Total_Points'] = (
        batters['R'] * 1 +
        batters['TB'] * 1 +
        batters['RBI'] * 1 +
        batters['BB'] * 1 +
        batters['SO'] * -1 +  # SO is strikeouts
        batters['SB'] * 1
    )
    
    # 2. Apply Custom Pitcher Scoring
    # IP=3, H=-1, ER=-2, BB=-1, K=1, QS=1, W=2, L=-2, SV=5, HD=2
    pitchers['Total_Points'] = (
        pitchers['IP'] * 3 +
        pitchers['H'] * -1 +
        pitchers['ER'] * -2 +
        pitchers['BB'] * -1 +
        pitchers['SO'] * 1 + 
        pitchers['QS'] * 1 +
        pitchers['W'] * 2 +
        pitchers['L'] * -2 +
        pitchers['SV'] * 5 +
        pitchers['HLD'] * 2
    )

    # 3. Calculate Weekly Averages
    # 24 total weeks (20 regular season + 4 playoff weeks)
    batters['Weekly_Avg'] = batters['Total_Points'] / 24
    pitchers['Weekly_Avg'] = pitchers['Total_Points'] / 24
    
    # Add a Draft Status column
    batters['Drafted_By'] = "Available"
    pitchers['Drafted_By'] = "Available"
    
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

if st.sidebar.button("Draft Player"):
    if player_type == "Batter":
        st.session_state.batters.loc[st.session_state.batters['Name'] == selected_player, 'Drafted_By'] = selected_team
    else:
        st.session_state.pitchers.loc[st.session_state.pitchers['Name'] == selected_player, 'Drafted_By'] = selected_team
    st.sidebar.success(f"{selected_player} drafted by {selected_team}")

# --- MAIN DASHBOARD ---
tab1, tab2 = st.tabs(["Available Players", "Team Rosters"])

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