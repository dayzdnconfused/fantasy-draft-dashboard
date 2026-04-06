import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re

st.set_page_config(page_title="In-Season Manager", layout="wide")

# --- 1. INITIALIZE MEMORY ---
if 'rosters' not in st.session_state:
    st.session_state['rosters'] = None

# --- 2. ESPN API DIRECT INTEGRATION ---
def fetch_espn_rosters():
    st.sidebar.header("1. Sync League Rosters")
    st.sidebar.markdown("Pull live rosters directly from ESPN's hidden API.")
    
    if st.sidebar.button("🔄 Sync Live ESPN Rosters"):
        with st.spinner("Connecting to ESPN Servers..."):
            try:
                league_id = st.secrets["ESPN_LEAGUE_ID"]
                url = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb/seasons/2026/segments/0/leagues/{league_id}?view=mRoster"
                
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Accept': 'application/json'
                }
                
                response = requests.get(url, headers=headers)
                
                if 'application/json' not in response.headers.get('Content-Type', '').lower():
                    st.sidebar.error("ESPN blocked the request. Double-check that your League Manager made the league 'Viewable to Public'.")
                elif response.status_code == 200:
                    data = response.json()
                    rostered_players = []
                    
                    for team in data.get('teams', []):
                        team_id = team.get('id')
                        for entry in team.get('roster', {}).get('entries', []):
                            player_name = entry['playerPoolEntry']['player']['fullName']
                            player_id = entry['playerId']
                            
                            rostered_players.append({
                                'Player_Name': player_name,
                                'ESPN_ID': player_id,
                                'Team_ID': team_id,
                                'Status': 'ROSTERED'
                            })
                    
                    df = pd.DataFrame(rostered_players)
                    df['Match_Name'] = df['Player_Name'].str.lower().str.replace(r'[^a-z ]', '', regex=True)
                    
                    st.session_state['rosters'] = df
                    st.sidebar.success(f"Successfully pulled {len(df)} rostered players directly from ESPN!")
                else:
                    st.sidebar.error(f"ESPN API Failed: Status {response.status_code}.")
            except Exception as e:
                st.sidebar.error(f"API Connection Error: {e}")

# --- 3. PITCHER LIST SCRAPER ---
def scrape_pitcher_list(url):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    try:
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.content, 'html.parser')
        text = soup.get_text()
        matches = re.findall(r'(\d{1,3})\.\s+([A-Z][a-zA-Z\']+\s[A-Z][a-zA-Z\']+)', text)
        if matches:
            return pd.DataFrame(matches, columns=['PL_Rank', 'Player_Name'])
        else:
            return pd.DataFrame()
    except Exception as e:
        st.error(f"Failed to scrape URL: {e}")
        return pd.DataFrame()

# --- 4. UI: PITCHER LIST SCANNER ---
def ui_pitcher_list_scanner():
    st.header("Pitcher List Waiver Wire Scanner")
    st.markdown("Paste the URL of this week's 'The List'. The app will compare the waiver wire to your current roster.")
    
    user_team_id = st.number_input("Your ESPN Team ID", min_value=1, max_value=20, value=3, key="pl_team_id")
    pl_url = st.text_input("Pitcher List Article URL:")
    
    if st.button("Scan the Wire"):
        if st.session_state['rosters'] is None:
            st.warning("Please click '🔄 Sync Live ESPN Rosters' in the sidebar first!")
            return
            
        with st.spinner("Scraping Pitcher List and cross-referencing rosters..."):
            pl_df = scrape_pitcher_list(pl_url)
            
            if not pl_df.empty:
                pl_df['PL_Rank'] = pd.to_numeric(pl_df['PL_Rank'])
                pl_df['Match_Name'] = pl_df['Player_Name'].str.lower().str.replace(r'[^a-z ]', '', regex=True)
                espn_df = st.session_state['rosters']
                
                available_df = pl_df[~pl_df['Match_Name'].isin(espn_df['Match_Name'])]
                my_roster_df = espn_df[espn_df['Team_ID'] == user_team_id]
                my_pl_df = pl_df[pl_df['Match_Name'].isin(my_roster_df['Match_Name'])]
                
                col1, col2 = st.columns(2)
                with col1:
                    st.success(f"🟢 {len(available_df)} Top 100 Pitchers Available")
                    st.dataframe(available_df[['PL_Rank', 'Player_Name']].sort_values('PL_Rank'), hide_index=True, use_container_width=True)
                with col2:
                    st.info(f"🔵 Your Rostered Top 100 Pitchers")
                    st.dataframe(my_pl_df[['PL_Rank', 'Player_Name']].sort_values('PL_Rank', ascending=False), hide_index=True, use_container_width=True)
            else:
                st.error("Could not extract player rankings. Pitcher List may have changed their formatting.")

# --- 5. UI: FANGRAPHS ROS OPTIMIZER ---
def ui_ros_optimizer():
    st.header("FanGraphs Rest-of-Season (ROS) Optimizer")
    st.markdown("Upload a raw FanGraphs Projection CSV. The app will calculate your league's custom points and find the best free agents.")
    
    user_team_id = st.number_input("Your ESPN Team ID", min_value=1, max_value=20, value=3, key="ros_team_id")
    proj_type = st.radio("What type of projections are you uploading?", ["Hitters", "Pitchers"])
    uploaded_file = st.file_uploader(f"Upload FanGraphs {proj_type} CSV", type=["csv"])
    
    if uploaded_file and st.button("Calculate ROS Points & Optimize Waivers"):
        if st.session_state['rosters'] is None:
            st.warning("Please click '🔄 Sync Live ESPN Rosters' in the sidebar first!")
            return
            
        with st.spinner("Crunching the custom scoring formula..."):
            try:
                proj_df = pd.read_csv(uploaded_file)
                
                if 'Name' not in proj_df.columns:
                    st.error("Could not find a 'Name' column in the CSV. Make sure this is a FanGraphs export.")
                    return
                    
                proj_df['Match_Name'] = proj_df['Name'].str.lower().str.replace(r'[^a-z ]', '', regex=True)
                
                # --- HITTER MATH ---
                if proj_type == "Hitters":
                    req_cols = ['H', '2B', '3B', 'HR', 'BB', 'R', 'RBI', 'SB', 'SO']
                    missing = [col for col in req_cols if col not in proj_df.columns]
                    if missing:
                        st.error(f"Missing columns for hitter math: {missing}")
                        return
                        
                    # Calculate Singles (1B) since FanGraphs doesn't provide it
                    proj_df['1B'] = proj_df['H'] - proj_df['2B'] - proj_df['3B'] - proj_df['HR']
                    
                    # Apply your exact points formula
                    proj_df['Total_Bases'] = proj_df['1B'] + (proj_df['2B'] * 2) + (proj_df['3B'] * 3) + (proj_df['HR'] * 4)
                    proj_df['ROS_Points'] = proj_df['Total_Bases'] + proj_df['BB'] + proj_df['R'] + proj_df['RBI'] + proj_df['SB'] - proj_df['SO']
                    display_cols = ['Name', 'ROS_Points', 'Total_Bases', 'HR', 'SB', 'SO']
                
                # --- PITCHER MATH ---
                else: 
                    req_cols = ['IP', 'ER', 'SO', 'W', 'L', 'SV', 'HLD', 'H', 'BB']
                    missing = [col for col in req_cols if col not in proj_df.columns]
                    if missing:
                        st.error(f"Missing columns for pitcher math: {missing}")
                        return
                        
                    # Apply your exact points formula
                    proj_df['ROS_Points'] = (proj_df['IP'] * 3) - (proj_df['ER'] * 2) + proj_df['SO'] + (proj_df['W'] * 3) - (proj_df['L'] * 3) + (proj_df['SV'] * 5) + (proj_df['HLD'] * 3) - proj_df['H'] - proj_df['BB']
                    display_cols = ['Name', 'ROS_Points', 'IP', 'SO', 'ER', 'SV', 'HLD']

                # Clean up formatting
                proj_df['ROS_Points'] = proj_df['ROS_Points'].round(1)

                # Cross-reference with live ESPN rosters
                espn_df = st.session_state['rosters']
                
                available_df = proj_df[~proj_df['Match_Name'].isin(espn_df['Match_Name'])]
                my_roster_df = espn_df[espn_df['Team_ID'] == user_team_id]
                my_proj_df = proj_df[proj_df['Match_Name'].isin(my_roster_df['Match_Name'])]
                
                col1, col2 = st.columns(2)
                
                with col1:
                    st.success(f"🟢 Top 50 Available Free Agents (Projected Points)")
                    st.dataframe(
                        available_df[display_cols].sort_values('ROS_Points', ascending=False).head(50), 
                        hide_index=True,
                        use_container_width=True
                    )
                    
                with col2:
                    st.info(f"🔵 Your Current Roster (Projected Points)")
                    # Sorted ascending so your lowest projected point scorers are at the top (drop candidates)
                    st.dataframe(
                        my_proj_df[display_cols].sort_values('ROS_Points', ascending=True), 
                        hide_index=True,
                        use_container_width=True
                    )
                    
            except Exception as e:
                st.error(f"Error processing CSV: {e}")

# --- APP EXECUTION ---
fetch_espn_rosters()

if st.session_state['rosters'] is not None:
    st.markdown("---")
    # Build the Tab layout
    tab1, tab2 = st.tabs(["🔥 Pitcher List Scanner", "📈 FanGraphs ROS Optimizer"])
    
    with tab1:
        ui_pitcher_list_scanner()
        
    with tab2:
        ui_ros_optimizer()
else:
    st.info("👈 Click the Sync button in the sidebar to connect to your ESPN league.")