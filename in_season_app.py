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
                # Grab credentials from secrets
                league_id = st.secrets["ESPN_LEAGUE_ID"]
                espn_s2 = st.secrets["ESPN_S2"]
                swid = st.secrets["SWID"]
                
                # ESPN's hidden backend API URL for 2026 Rosters
                url = f"https://fantasy.espn.com/apis/v3/games/flb/seasons/2026/segments/0/leagues/{league_id}?view=mRoster"
                
                # We pass the cookies to prove you have access to the league
                cookies = {"espn_s2": espn_s2, "swid": swid}
                
                response = requests.get(url, cookies=cookies)
                
                if response.status_code == 200:
                    data = response.json()
                    
                    rostered_players = []
                    
                    # Dig through the JSON payload to extract teams and players
                    for team in data.get('teams', []):
                        team_id = team.get('id')
                        # 'entries' holds the list of players on that specific team
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
                    
                    # Save to session state so the rest of the app can use it
                    st.session_state.rosters = df
                    st.sidebar.success(f"Successfully pulled {len(df)} rostered players directly from ESPN!")
                    
                else:
                    st.sidebar.error(f"ESPN API Failed: Status {response.status_code}. Check your S2 and SWID cookies.")
                    
            except Exception as e:
                st.sidebar.error(f"API Connection Error: {e}")

# Call the function in your app
fetch_espn_rosters()

# --- HOW IT CHANGES OUR LOGIC ---
if 'rosters' in st.session_state:
    st.markdown("### Your Live League Database is Active")
    # Instead of looking for "FA" or "WA" in a CSV, our logic is now much simpler:
    # If a player from Pitcher List is NOT in this ESPN dataframe, they are a Free Agent!

# --- FEATURE 1: Pitcher List URL Scanner ---
def scrape_pitcher_list(url):
    """Scrapes a Pitcher List article URL to extract player names and ranks."""
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    try:
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Pitcher List often formats the Top 100 as text like "1. Spencer Strider – ..." or in specific tables.
        # This regex looks for patterns like "1. First Last" in the article text.
        text = soup.get_text()
        matches = re.findall(r'(\d{1,3})\.\s+([A-Z][a-zA-Z\']+\s[A-Z][a-zA-Z\']+)', text)
        
        if matches:
            return pd.DataFrame(matches, columns=['PL_Rank', 'Player_Name'])
        else:
            return pd.DataFrame()
            
    except Exception as e:
        st.error(f"Failed to scrape URL: {e}")
        return pd.DataFrame()

def ui_pitcher_list_scanner():
    st.header("Pitcher List Waiver Wire Scanner")
    st.markdown("Paste the URL of this week's 'The List' or 'Hitter List'. The app will scrape the rankings and cross-reference them against your ESPN waiver wire.")
    
    pl_url = st.text_input("Pitcher List Article URL:")
    
    if st.button("Scan the Wire"):
        if 'rosters' not in st.session_state:
            st.warning("Please upload your ESPN rosters in the sidebar first!")
            return
            
        with st.spinner("Scraping Pitcher List and analyzing your waiver wire..."):
            pl_df = scrape_pitcher_list(pl_url)
            
            if not pl_df.empty:
                # Convert ranks to numeric for sorting
                pl_df['PL_Rank'] = pd.to_numeric(pl_df['PL_Rank'])
                
                # Standardize names for matching
                espn_df = st.session_state.rosters
                espn_df['Match_Name'] = espn_df['Player'].str.lower().str.replace(r'[^a-z ]', '', regex=True)
                pl_df['Match_Name'] = pl_df['Player_Name'].str.lower().str.replace(r'[^a-z ]', '', regex=True)
                
                # Merge the lists
                merged = pd.merge(pl_df, espn_df, on='Match_Name', how='inner')
                
                # Filter strictly for players available on the waiver wire (e.g., Status == 'FA' or 'WA')
                # Note: You will need to adjust 'Status' and 'FA' to match ESPN's exact CSV column headers
                if 'Status' in merged.columns:
                    available = merged[merged['Status'].isin(['FA', 'WA'])]
                    
                    st.success("Analysis Complete! Here are the highest-ranked available players in your league:")
                    st.dataframe(
                        available[['PL_Rank', 'Player_Name', 'Status', 'Pos']], 
                        hide_index=True,
                        use_container_width=True
                    )
                else:
                    st.info("Rankings scraped! (Could not find a 'Status' column in your ESPN CSV to filter free agents).")
                    st.dataframe(merged)
            else:
                st.error("Could not extract player rankings from that URL. Pitcher List may have changed their formatting.")

# --- APP EXECUTION ---
load_rosters()

if 'rosters' in st.session_state:
    st.markdown("---")
    ui_pitcher_list_scanner()
else:
    st.info("👈 Upload your ESPN roster CSV in the sidebar to get started.")