import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re

st.set_page_config(page_title="In-Season Manager", layout="wide")

# --- ESPN API DIRECT INTEGRATION ---
def fetch_espn_rosters():
    st.sidebar.header("1. Sync League Rosters")
    st.sidebar.markdown("Pull live rosters directly from ESPN's hidden API.")
    
    if st.sidebar.button("🔄 Sync Live ESPN Rosters"):
        with st.spinner("Connecting to ESPN Servers..."):
            try:
                league_id = st.secrets["ESPN_LEAGUE_ID"]
                espn_s2 = st.secrets["ESPN_S2"]
                swid = st.secrets["SWID"]
                
                url = f"https://fantasy.espn.com/apis/v3/games/flb/seasons/2026/segments/0/leagues/{league_id}?view=mRoster"
                cookies = {"espn_s2": espn_s2, "swid": swid}
                
                response = requests.get(url, cookies=cookies)
                
                if response.status_code == 200:
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
                    # Create a cleaned-up name column for perfect cross-referencing
                    df['Match_Name'] = df['Player_Name'].str.lower().str.replace(r'[^a-z ]', '', regex=True)
                    
                    # Save to session state
                    st.session_state.rosters = df
                    st.sidebar.success(f"Successfully pulled {len(df)} rostered players directly from ESPN!")
                else:
                    st.sidebar.error(f"ESPN API Failed: Status {response.status_code}. Check your S2 and SWID cookies.")
            except Exception as e:
                st.sidebar.error(f"API Connection Error: {e}")

# --- PITCHER LIST SCRAPER ---
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

# --- THE WAIVER WIRE SCANNER UI ---
def ui_pitcher_list_scanner():
    st.header("Pitcher List Waiver Wire Scanner")
    st.markdown("Paste the URL of this week's 'The List'. The app will scrape the rankings and find exactly who is sitting on your waiver wire.")
    
    pl_url = st.text_input("Pitcher List Article URL:")
    
    if st.button("Scan the Wire"):
        if 'rosters' not in st.session_state:
            st.warning("Please click 'Sync Live ESPN Rosters' in the sidebar first!")
            return
            
        with st.spinner("Scraping Pitcher List and isolating Free Agents..."):
            pl_df = scrape_pitcher_list(pl_url)
            
            if not pl_df.empty:
                pl_df['PL_Rank'] = pd.to_numeric(pl_df['PL_Rank'])
                pl_df['Match_Name'] = pl_df['Player_Name'].str.lower().str.replace(r'[^a-z ]', '', regex=True)
                
                espn_df = st.session_state.rosters
                
                # THE MAGIC LOGIC: Find Pitcher List players who are NOT on an ESPN roster
                available_df = pl_df[~pl_df['Match_Name'].isin(espn_df['Match_Name'])]
                
                st.success(f"Found {len(available_df)} Top 100 Pitchers currently available in your league!")
                st.dataframe(
                    available_df[['PL_Rank', 'Player_Name']].sort_values('PL_Rank'), 
                    hide_index=True,
                    use_container_width=True
                )
            else:
                st.error("Could not extract player rankings. Pitcher List may have changed their formatting.")

# --- APP EXECUTION ---
fetch_espn_rosters()

if 'rosters' in st.session_state:
    st.markdown("---")
    ui_pitcher_list_scanner()
else:
    st.info("👈 Click the Sync button in the sidebar to connect to your ESPN league.")