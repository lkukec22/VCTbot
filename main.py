import discord
from discord.ext import commands, tasks
from discord import app_commands, ui
import os
import aiohttp
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import logging
import datetime
import threading
import math
import random
import time
import sqlite3
import pytz
from dateutil import parser
from fuzzywuzzy import process

from http.server import HTTPServer, BaseHTTPRequestHandler

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("valorant_bot")

# Load environment variables
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# Check if token exists
if not TOKEN:
    logger.error("No Discord token found. Please set the DISCORD_TOKEN in your .env file.")
    exit(1)

# Set up intents
intents = discord.Intents.default()
intents.guilds = True  # Required for slash commands

# Create bot instance with slash commands only
class ValorantBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="", intents=intents, help_command=None)

    async def setup_hook(self):
        # Force sync all slash commands with Discord
        try:
            synced = await self.tree.sync()
            logger.info(f"Slash commands synced: {len(synced)} commands")
            for cmd in synced:
                logger.info(f"Synced command: {cmd.name}")
        except Exception as e:
            logger.error(f"Error syncing slash commands: {e}")

bot = ValorantBot()

# Cache for storing data to avoid frequent requests
results_cache = {
    'recent_data': None,
    'upcoming_data': None,
    'team_data': {},
    'tournament_data': {},
    'timestamp': None,
    'scraping_failures': 0,
    'last_success': None
}

# Team aliases for common team name variations
TEAM_ALIASES = {
    'c9': 'cloud9',
    '100t': '100 thieves',
    'sen': 'sentinels',
    'eg': 'evil geniuses',
    'tl': 'liquid',
    'fnc': 'fnatic',
    'prx': 'paper rex',
    'geng': 'gen.g',
    'leviatan': 'leviatán',
    'levi': 'leviatán',
    'krü': 'kru',
    'krue': 'kru',
}

# Team colors for popular Valorant teams
TEAM_COLORS = {
    'sentinels': 0xFF0000,
    'cloud9': 0x1DA1F2,
    'fnatic': 0xFFA500,
    'liquid': 0x000080,
    '100 thieves': 0xFF0000,
    'nrg': 0x000000,
    'evil geniuses': 0x0000FF,
    'g2': 0x000000,
    'faze': 0xFF0000,
    'drx': 0x0000FF,
    'paper rex': 0xFFD700,
    't1': 0xFF0000,
    'gen.g': 0xFFD700,
    'loud': 0x00FF00,
    'leviatán': 0x800080,
    'kru': 0x00FFFF,
}

# Tournament aliases for common tournament name variations
TOURNAMENT_ALIASES = {
    'vct': 'vct',
    'masters': 'masters',
    'champs': 'champions',
    'gc': 'game changers',
    'gamechangers': 'game changers',
    'challengers': 'challengers',
    'chal': 'challengers',
    'asc': 'ascension',
}

# Tournament colors
TOURNAMENT_COLORS = {
    'vct': 0xFF4500,
    'masters': 0x9370DB,
    'champions': 0xFFD700,
    'ascension': 0x32CD32,
    'game changers': 0xFF69B4,
    'challengers': 0x4169E1,
}

# Timezone utilities
def get_timezone_list():
    """Get a list of common timezones"""
    common_timezones = [
        'UTC', 'US/Eastern', 'US/Central', 'US/Mountain', 'US/Pacific',
        'Europe/London', 'Europe/Paris', 'Europe/Berlin', 'Europe/Moscow',
        'Asia/Tokyo', 'Asia/Shanghai', 'Asia/Seoul', 'Australia/Sydney',
        'America/Sao_Paulo', 'America/Los_Angeles', 'America/New_York'
    ]
    return common_timezones

def is_valid_timezone(timezone_str):
    """Check if a timezone string is valid"""
    try:
        pytz.timezone(timezone_str)
        return True
    except pytz.exceptions.UnknownTimeZoneError:
        return False

def parse_match_time(time_str, server_timezone='UTC'):
    """Parse match time string and convert to a datetime object"""
    try:
        # Try to parse the time string
        if 'in ' in time_str.lower():
            # Relative time (e.g., "in 2h")
            time_parts = time_str.lower().replace('in ', '').strip().split()
            if len(time_parts) >= 1:
                value = int(''.join(filter(str.isdigit, time_parts[0])))
                unit = ''.join(filter(str.isalpha, time_parts[0]))

                now = datetime.datetime.now(pytz.timezone(server_timezone))

                if unit.startswith('h'):
                    match_time = now + datetime.timedelta(hours=value)
                elif unit.startswith('m'):
                    match_time = now + datetime.timedelta(minutes=value)
                elif unit.startswith('d'):
                    match_time = now + datetime.timedelta(days=value)
                else:
                    # Default to hours if unit is unclear
                    match_time = now + datetime.timedelta(hours=value)

                return match_time
        else:
            # Try to parse as absolute time
            match_time = parser.parse(time_str)

            # If no timezone info, assume UTC
            if match_time.tzinfo is None:
                match_time = match_time.replace(tzinfo=pytz.UTC)

            return match_time
    except Exception as e:
        logger.error(f"Error parsing match time '{time_str}': {e}")
        return None

def format_match_time(dt, target_timezone='UTC'):
    """Format a datetime object for display in the specified timezone"""
    if dt is None:
        return "Time unknown"

    try:
        # Ensure datetime has timezone info
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=pytz.UTC)

        # Convert to target timezone
        local_dt = dt.astimezone(pytz.timezone(target_timezone))

        # Format the time
        formatted_time = local_dt.strftime("%Y-%m-%d %H:%M %Z")

        # Calculate time difference from now
        now = datetime.datetime.now(pytz.timezone(target_timezone))
        time_diff = local_dt - now

        # Format relative time
        if time_diff.total_seconds() < 0:
            relative = "(Match has already started)"
        elif time_diff.total_seconds() < 3600:  # Less than 1 hour
            minutes = int(time_diff.total_seconds() / 60)
            relative = f"(in {minutes} minutes)"
        elif time_diff.total_seconds() < 86400:  # Less than 1 day
            hours = int(time_diff.total_seconds() / 3600)
            relative = f"(in {hours} hours)"
        else:
            days = int(time_diff.total_seconds() / 86400)
            relative = f"(in {days} days)"

        return f"{formatted_time} {relative}"
    except Exception as e:
        logger.error(f"Error formatting match time: {e}")
        return "Time format error"

# Function to resolve team name from input, handling aliases and fuzzy matching
def resolve_team_name(team_input):
    """Resolve team name from input, handling aliases and fuzzy matching"""
    if not team_input:
        return team_input

    team_lower = team_input.lower()

    # Check for direct alias
    if team_lower in TEAM_ALIASES:
        logger.info(f"Resolved team alias: {team_input} -> {TEAM_ALIASES[team_lower]}")
        return TEAM_ALIASES[team_lower]

    # Check for direct match in team colors
    for team in TEAM_COLORS.keys():
        if team_lower == team or team_lower in team:
            return team

    # Try fuzzy matching
    matches = process.extractOne(team_lower, list(TEAM_COLORS.keys()), score_cutoff=80)
    if matches:
        logger.info(f"Fuzzy matched team: {team_input} -> {matches[0]} (score: {matches[1]})")
        return matches[0]

    # Return original if no match found
    return team_input

# Function to resolve tournament name from input, handling aliases and fuzzy matching
def resolve_tournament_name(tournament_input):
    """Resolve tournament name from input, handling aliases and fuzzy matching"""
    if not tournament_input:
        return tournament_input

    tournament_lower = tournament_input.lower()

    # Check for direct alias
    if tournament_lower in TOURNAMENT_ALIASES:
        logger.info(f"Resolved tournament alias: {tournament_input} -> {TOURNAMENT_ALIASES[tournament_lower]}")
        return TOURNAMENT_ALIASES[tournament_lower]

    # Check for direct match in tournament colors
    for tournament in TOURNAMENT_COLORS.keys():
        if tournament_lower == tournament or tournament_lower in tournament:
            return tournament

    # Try fuzzy matching
    matches = process.extractOne(tournament_lower, list(TOURNAMENT_COLORS.keys()), score_cutoff=80)
    if matches:
        logger.info(f"Fuzzy matched tournament: {tournament_input} -> {matches[0]} (score: {matches[1]})")
        return matches[0]

    # Return original if no match found
    return tournament_input

# Function to get color for a team or tournament
def get_entity_color(name, is_tournament=False):
    """Get the color for a team or tournament, or a default color if not found"""
    if not name:
        return discord.Color.red() if not is_tournament else discord.Color.blue()

    name_lower = name.lower()

    # Check for exact matches first
    if is_tournament:
        for key, color in TOURNAMENT_COLORS.items():
            if key == name_lower or key in name_lower:
                return discord.Color(color)
    else:
        for key, color in TEAM_COLORS.items():
            if key == name_lower or key in name_lower:
                return discord.Color(color)

    # Return a random but consistent color based on the name
    random.seed(name_lower)
    return discord.Color(random.randint(0, 0xFFFFFF))

async def get_valorant_results(limit=5, upcoming=False, team=None, tournament=None):
    """
    Scrapes vlr.gg for Valorant match results or upcoming matches.

    Args:
        limit (int): Maximum number of results to return
        upcoming (bool): If True, fetch upcoming matches instead of results
        team (str): If provided, filter results for this specific team
        tournament (str): If provided, filter results for this specific tournament

    Returns:
        list: List of formatted match results or None if an error occurred
    """
    # Check cache first (valid for 5 minutes)
    current_time = datetime.datetime.now()
    if results_cache['timestamp'] is not None and (current_time - results_cache['timestamp']).total_seconds() < 300:
        # If searching for a specific tournament
        if tournament:
            tournament_key = tournament.lower()
            if tournament_key in results_cache['tournament_data']:
                logger.info(f"Using cached results for tournament {tournament}")
                return results_cache['tournament_data'][tournament_key][:limit]
        # If searching for a specific team
        elif team:
            team_key = team.lower()
            if team_key in results_cache['team_data']:
                logger.info(f"Using cached results for team {team}")
                return results_cache['team_data'][team_key][:limit]
        # Otherwise use general results
        elif upcoming and results_cache['upcoming_data'] is not None:
            logger.info("Using cached upcoming matches")
            return results_cache['upcoming_data'][:limit]
        elif not upcoming and results_cache['recent_data'] is not None:
            logger.info("Using cached recent results")
            return results_cache['recent_data'][:limit]

    # Different URLs for upcoming matches vs results
    if upcoming:
        url = "https://www.vlr.gg/matches"
    else:
        url = "https://www.vlr.gg/matches/results"

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    try:
        logger.info(f"Fetching match results from {url}")
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10) as response:
                response.raise_for_status()
                html = await response.text()
                soup = BeautifulSoup(html, 'html.parser')

        # Find match elements using multiple approaches for robustness
        match_elements = []
        match_cards = soup.select('a.match-item, a.wf-module-item')
        if match_cards:
            logger.info(f"Found {len(match_cards)} match cards using class selectors")
            match_elements = match_cards

        # Fallback: Try finding by date headers
        if not match_elements:
            logger.info("Class selectors failed, trying date header approach")
            date_headers = soup.find_all(lambda tag: tag.name == 'div' and
                                        (tag.text.strip().endswith('Today') or
                                         tag.text.strip().endswith('Yesterday') or
                                         'ago' in tag.text.strip().lower()))

            logger.info(f"Found {len(date_headers)} date headers")

            for header in date_headers[:3]:
                current = header.next_sibling

                while current and (not isinstance(current, type(header)) or
                                not (current.text.strip().endswith('Today') or
                                     current.text.strip().endswith('Yesterday') or
                                     'ago' in current.text.strip().lower())):
                    if current.name == 'a' and current.get('href', '').startswith('/'):
                        match_elements.append(current)
                    current = current.next_sibling
                    if not current:
                        break

        # Last resort: Find by URL pattern
        if not match_elements:
            logger.info("Date header approach failed, trying URL pattern fallback")
            all_links = soup.find_all('a')
            match_elements = [link for link in all_links if link.get('href', '').startswith('/') and
                             len(link.get('href', '').split('/')) > 1 and
                             any(x.isdigit() for x in link.get('href', '').split('/')[1].split('-'))]

        if not match_elements:
            logger.warning("No match elements found. Website structure might have changed.")
            return []

        logger.info(f"Found {len(match_elements)} match elements")

        results = []
        for match in match_elements[:limit]:
            try:
                # Extract match data
                all_text = [text for text in match.stripped_strings]
                logger.info(f"Match text content: {all_text}")

                # Initialize with default values
                team1 = "Unknown"
                team2 = "Unknown"
                score1 = "?"
                score2 = "?"
                event_name = "Unknown Event"
                event_stage = ""
                match_time = "TBD"

                # Try to extract data using CSS selectors
                try:
                    team_elements = match.select('.match-item-vs-team-name, .wf-title-med')
                    if len(team_elements) >= 2:
                        team1 = team_elements[0].get_text(strip=True)
                        team2 = team_elements[1].get_text(strip=True)

                    score_elements = match.select('.match-item-vs-team-score, .match-item-score')
                    if len(score_elements) >= 2:
                        score1 = score_elements[0].get_text(strip=True) or "?"
                        score2 = score_elements[1].get_text(strip=True) or "?"

                    event_element = match.select_one('.match-item-event, .match-item-league, .wf-card-sub')
                    if event_element:
                        event_text = event_element.get_text(strip=True)
                        if '–' in event_text or '-' in event_text:
                            separator = '–' if '–' in event_text else '-'
                            event_parts = event_text.split(separator, 1)
                            event_stage = event_parts[0].strip()
                            event_name = event_parts[1].strip() if len(event_parts) > 1 else event_parts[0].strip()
                        else:
                            event_name = event_text

                    if upcoming:
                        time_element = match.select_one('.match-item-time, .match-item-eta, .wf-card-micro')
                        if time_element:
                            match_time = time_element.get_text(strip=True)
                except Exception as e:
                    logger.warning(f"Error extracting data with selectors: {e}")

                # Fallback to text pattern analysis if selectors didn't work
                if team1 == "Unknown" or team2 == "Unknown":
                    if len(all_text) >= 6:
                        # Pattern: [time, team1, score1, team2, score2, status, ...]
                        if all_text[0].count(':') == 1 and all_text[0].count(' ') <= 1:  # Looks like a time
                            team1 = all_text[1]
                            score1 = all_text[2] if all_text[2].isdigit() or all_text[2] == "?" else score1
                            team2 = all_text[3]
                            score2 = all_text[4] if all_text[4].isdigit() or all_text[4] == "?" else score2
                        # Another common pattern
                        elif len(all_text) >= 10 and any(x.isdigit() for x in all_text[:5]):
                            for i, text in enumerate(all_text[:4]):
                                if len(text) > 2 and not text.isdigit() and not ":" in text:
                                    team1 = text
                                    if i+1 < len(all_text) and (all_text[i+1].isdigit() or all_text[i+1] == "?"):
                                        score1 = all_text[i+1]
                                    if i+2 < len(all_text) and len(all_text[i+2]) > 2 and not all_text[i+2].isdigit():
                                        team2 = all_text[i+2]
                                    if i+3 < len(all_text) and (all_text[i+3].isdigit() or all_text[i+3] == "?"):
                                        score2 = all_text[i+3]
                                    break

                # Extract event info if not found by selectors
                if event_name == "Unknown Event":
                    status_keywords = ["Completed", "Live", "Upcoming", "Scheduled"]
                    for keyword in status_keywords:
                        keyword_index = -1
                        for i, text in enumerate(all_text):
                            if keyword in text:
                                keyword_index = i
                                break

                        if keyword_index != -1 and keyword_index + 2 < len(all_text):
                            for j in range(keyword_index + 1, min(keyword_index + 5, len(all_text))):
                                event_text = all_text[j]
                                if len(event_text) > 3 and not event_text.isdigit() and ":" not in event_text:
                                    if '–' in event_text or '-' in event_text:
                                        separator = '–' if '–' in event_text else '-'
                                        event_parts = event_text.split(separator, 1)
                                        event_stage = event_parts[0].strip()
                                        event_name = event_parts[1].strip() if len(event_parts) > 1 else event_parts[0].strip()
                                    else:
                                        event_name = event_text.strip()
                                    break

                # Extract match time for upcoming matches if not found by selectors
                if upcoming and match_time == "TBD" and len(all_text) > 0:
                    for text in all_text[:3]:
                        if ":" in text and text.count(" ") <= 1:
                            match_time = text
                            break

                # Format result
                result = {
                    'team1': team1,
                    'team2': team2,
                    'score1': score1,
                    'score2': score2,
                    'event': event_name,
                    'stage': event_stage,
                    'time': match_time if upcoming else "",
                    'url': f"https://www.vlr.gg{match.get('href', '')}"
                }

                results.append(result)
            except Exception as e:
                logger.error(f"Error parsing match element: {e}")

        # Filter results for a specific tournament if requested
        if tournament:
            tournament_key = tournament.lower()
            tournament_results = []

            for result in results:
                # Check if tournament name is in the event name (case insensitive)
                if tournament_key in result['event'].lower():
                    tournament_results.append(result)

            # Update tournament-specific cache
            results_cache['tournament_data'][tournament_key] = tournament_results
            results = tournament_results
        # Filter results for a specific team if requested
        elif team:
            team_key = team.lower()
            team_results = []

            for result in results:
                # Check if team name is in either team1 or team2 (case insensitive)
                if (team_key in result['team1'].lower() or
                    team_key in result['team2'].lower()):
                    team_results.append(result)

            # Update team-specific cache
            results_cache['team_data'][team_key] = team_results
            results = team_results

        # Update general cache
        if upcoming:
            results_cache['upcoming_data'] = results
        elif not (team or tournament):  # Only update recent_data if not a filtered search
            results_cache['recent_data'] = results

        results_cache['timestamp'] = current_time
        results_cache['scraping_failures'] = 0
        results_cache['last_success'] = current_time

        return results

    except aiohttp.ClientResponseError as e:
        results_cache['scraping_failures'] += 1
        logger.error(f"Error in response from vlr.gg: {e.status} {e.message} (Failure #{results_cache['scraping_failures']})")
        if results_cache['scraping_failures'] >= 5:
            logger.critical(f"ALERT: {results_cache['scraping_failures']} consecutive scraping failures! Website structure may have changed.")
        return None
    except aiohttp.ClientConnectorError as e:
        results_cache['scraping_failures'] += 1
        logger.error(f"Connection error when accessing vlr.gg: {e} (Failure #{results_cache['scraping_failures']})")
        return None
    except aiohttp.ClientTimeout as e:
        results_cache['scraping_failures'] += 1
        logger.error(f"Request to vlr.gg timed out: {e} (Failure #{results_cache['scraping_failures']})")
        return None
    except aiohttp.ClientError as e:
        results_cache['scraping_failures'] += 1
        logger.error(f"Client error when accessing vlr.gg: {e} (Failure #{results_cache['scraping_failures']})")
        return None
    except Exception as e:
        results_cache['scraping_failures'] += 1
        logger.error(f"Unexpected error: {e} (Failure #{results_cache['scraping_failures']})")
        return None

async def get_match_details(match_url):
    """
    Scrapes detailed information about a specific match from vlr.gg.

    Args:
        match_url (str): URL of the match to scrape

    Returns:
        dict: Detailed match information or None if an error occurred
    """
    # Ensure the URL is complete
    if not match_url.startswith('http'):
        match_url = f"https://www.vlr.gg{match_url}"

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    try:
        logger.info(f"Fetching match details from {match_url}")
        async with aiohttp.ClientSession() as session:
            async with session.get(match_url, headers=headers, timeout=10) as response:
                response.raise_for_status()
                html = await response.text()
                soup = BeautifulSoup(html, 'html.parser')

        # Initialize match details
        match_details = {
            'team1': "Unknown",
            'team2': "Unknown",
            'score1': "?",
            'score2': "?",
            'status': "Unknown",
            'event': "Unknown Event",
            'stage': "",
            'time': "TBD",
            'maps': [],
            'url': match_url
        }

        # Extract team names
        team_headers = soup.select('.match-header-vs-team-name')
        if len(team_headers) >= 2:
            match_details['team1'] = team_headers[0].get_text(strip=True)
            match_details['team2'] = team_headers[1].get_text(strip=True)

        # Extract scores
        score_elements = soup.select('.match-header-vs-score-score')
        if len(score_elements) >= 2:
            match_details['score1'] = score_elements[0].get_text(strip=True) or "?"
            match_details['score2'] = score_elements[1].get_text(strip=True) or "?"

        # Extract event info
        event_element = soup.select_one('.match-header-event-series')
        if event_element:
            event_text = event_element.get_text(strip=True)
            if '–' in event_text or '-' in event_text:
                separator = '–' if '–' in event_text else '-'
                event_parts = event_text.split(separator, 1)
                match_details['stage'] = event_parts[0].strip()
                match_details['event'] = event_parts[1].strip() if len(event_parts) > 1 else event_parts[0].strip()
            else:
                match_details['event'] = event_text

        # Extract match status
        status_element = soup.select_one('.match-header-vs-note')
        if status_element:
            match_details['status'] = status_element.get_text(strip=True)

        # Extract match time
        time_element = soup.select_one('.match-header-date')
        if time_element:
            match_details['time'] = time_element.get_text(strip=True)

        # Extract map details
        map_elements = soup.select('.vm-stats-game')
        for map_element in map_elements:
            map_name_element = map_element.select_one('.map-name')
            map_name = map_name_element.get_text(strip=True) if map_name_element else "Unknown Map"

            map_score_elements = map_element.select('.score')
            map_score1 = "?"
            map_score2 = "?"
            if len(map_score_elements) >= 2:
                map_score1 = map_score_elements[0].get_text(strip=True)
                map_score2 = map_score_elements[1].get_text(strip=True)

            match_details['maps'].append({
                'name': map_name,
                'score1': map_score1,
                'score2': map_score2
            })

        return match_details

    except aiohttp.ClientResponseError as e:
        logger.error(f"Error in response from vlr.gg: {e.status} {e.message}")
        return None
    except aiohttp.ClientConnectorError as e:
        logger.error(f"Connection error when accessing vlr.gg: {e}")
        return None
    except aiohttp.ClientTimeout as e:
        logger.error(f"Request to vlr.gg timed out: {e}")
        return None
    except aiohttp.ClientError as e:
        logger.error(f"Client error when accessing vlr.gg: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return None


async def check_scraping_health():
    """Check the health of the web scraping and alert if there are issues"""
    if results_cache['scraping_failures'] >= 5:
        app_info = await bot.application_info()
        owner = app_info.owner

        time_since_success = "Never" if results_cache['last_success'] is None else \
            f"{(datetime.datetime.now() - results_cache['last_success']).total_seconds() / 60:.1f} minutes ago"

        try:
            await owner.send(f"⚠️ **ALERT**: The VCT Results Bot has experienced {results_cache['scraping_failures']} consecutive scraping failures! " \
                           f"Last successful scrape: {time_since_success}. " \
                           f"The vlr.gg website structure may have changed and the bot needs maintenance.")
            logger.info(f"Sent scraping failure alert to bot owner {owner.name}")
        except Exception as e:
            logger.error(f"Failed to send alert to bot owner: {e}")

@tasks.loop(hours=6)
async def health_check_task():
    """Periodic task to check the health of the web scraping"""
    logger.info("Running periodic scraping health check")
    await check_scraping_health()

    if results_cache['scraping_failures'] >= 3:
        logger.info("Attempting test scrape to verify functionality")
        test_results = await get_valorant_results(limit=1)
        if test_results is not None and len(test_results) > 0:
            logger.info("Test scrape successful, scraping appears to be working again")
        else:
            logger.warning("Test scrape failed, scraping issues persist")

@tasks.loop(minutes=5)
async def reminder_check_task():
    """Periodic task to check for match reminders"""
    logger.info("Checking for match reminders")

    # Get pending reminders
    reminders = get_pending_reminders()
    if not reminders:
        return

    logger.info(f"Found {len(reminders)} pending reminders")

    # Current time in UTC
    now = datetime.datetime.now(pytz.UTC)

    for reminder in reminders:
        try:
            # Parse the match time
            match_time = datetime.datetime.fromisoformat(reminder['match_time'])

            # Calculate time until match
            time_until_match = match_time - now

            # If match is within 15 minutes or already started (but not more than 30 minutes ago)
            if time_until_match.total_seconds() <= 900 and time_until_match.total_seconds() > -1800:
                # Get the channel
                channel = bot.get_channel(int(reminder['channel_id']))
                if channel is None:
                    logger.warning(f"Channel {reminder['channel_id']} not found for reminder {reminder['id']}")
                    mark_reminder_as_sent(reminder['id'])
                    continue

                # Get the user
                user = await bot.fetch_user(int(reminder['user_id']))
                if user is None:
                    logger.warning(f"User {reminder['user_id']} not found for reminder {reminder['id']}")
                    mark_reminder_as_sent(reminder['id'])
                    continue

                # Create embed
                embed = discord.Embed(
                    title=f"{reminder['team1']} vs {reminder['team2']} - Match Reminder",
                    description=f"The match is about to start!",
                    color=discord.Color.gold(),
                    url=reminder['match_url']
                )

                # Add match time
                if time_until_match.total_seconds() > 0:
                    minutes_until = int(time_until_match.total_seconds() / 60)
                    embed.add_field(
                        name="Time Until Match",
                        value=f"Approximately {minutes_until} minutes",
                        inline=False
                    )
                else:
                    embed.add_field(
                        name="Status",
                        value="Match has started!",
                        inline=False
                    )

                # Send the reminder
                await channel.send(f"{user.mention} Here's your match reminder!", embed=embed)
                logger.info(f"Sent reminder {reminder['id']} to user {user.name} for match {reminder['team1']} vs {reminder['team2']}")

                # Mark the reminder as sent
                mark_reminder_as_sent(reminder['id'])
        except Exception as e:
            logger.error(f"Error processing reminder {reminder['id']}: {e}")

@reminder_check_task.before_loop
async def before_reminder_check():
    await bot.wait_until_ready()

@health_check_task.before_loop
async def before_health_check():
    await bot.wait_until_ready()

@bot.event
async def on_ready():
    """Event triggered when the bot is ready"""
    logger.info(f'Logged in as {bot.user.name} (ID: {bot.user.id})')
    logger.info('Bot is ready!')

    # Set bot status
    await bot.change_presence(activity=discord.Game(name="Use / commands | /help"))

    # Check scraping health on startup
    await check_scraping_health()

    # Start the periodic health check task
    health_check_task.start()

    # Start the keep-alive task to prevent Render from sleeping
    keep_alive.start()
    logger.info("Keep-alive task started")

    # Start the reminder check task
    reminder_check_task.start()
    logger.info("Reminder check task started")

    # Try to sync commands again on startup
    try:
        synced = await bot.tree.sync()
        logger.info(f"Commands synced on startup: {len(synced)} commands")
    except Exception as e:
        logger.error(f"Error syncing commands on startup: {e}")

class ResultsPaginator(ui.View):
    def __init__(self, results, upcoming=False, team=None, tournament=None, timeout=180):
        super().__init__(timeout=timeout)
        self.results = results
        self.upcoming = upcoming
        self.team = team
        self.tournament = tournament
        self.current_page = 0
        self.results_per_page = 5
        self.total_pages = math.ceil(len(results) / self.results_per_page)

        if self.total_pages <= 1:
            self.previous_button.disabled = True
            self.next_button.disabled = True

        # Only add reminder button for upcoming matches
        if not upcoming:
            self.remove_item(self.remind_button)

    @ui.button(label="Previous", style=discord.ButtonStyle.secondary, emoji="⬅️")
    async def previous_button(self, interaction: discord.Interaction, _: ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
            embed = await create_match_results_embed(
                self.get_current_page_results(),
                upcoming=self.upcoming,
                page_info=(self.current_page + 1, self.total_pages),
                team=self.team,
                tournament=self.tournament
            )

            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.defer()

    @ui.button(label="Next", style=discord.ButtonStyle.secondary, emoji="➡️")
    async def next_button(self, interaction: discord.Interaction, _: ui.Button):
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            embed = await create_match_results_embed(
                self.get_current_page_results(),
                upcoming=self.upcoming,
                page_info=(self.current_page + 1, self.total_pages),
                team=self.team,
                tournament=self.tournament
            )

            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.defer()

    @ui.button(label="Remind Me", style=discord.ButtonStyle.success, emoji="⏰")
    async def remind_button(self, interaction: discord.Interaction, _: ui.Button):
        # Get the current page results
        current_results = self.get_current_page_results()

        # Create a select menu with the matches
        select_options = []
        for i, match in enumerate(current_results):
            select_options.append(
                discord.SelectOption(
                    label=f"{match['team1']} vs {match['team2']}",
                    description=f"{match['time']} - {match['event']}",
                    value=str(i)
                )
            )

        # Create the select menu view
        view = MatchSelectView(current_results, interaction.user.id, interaction.channel_id)

        # Send the select menu as an ephemeral message
        await interaction.response.send_message(
            "Select a match to be reminded about:",
            view=view,
            ephemeral=True
        )

    async def on_timeout(self):
        """Called when the view times out"""
        for item in self.children:
            item.disabled = True

        try:
            if hasattr(self, 'message') and self.message:
                await self.message.edit(view=self)
        except Exception as e:
            logger.error(f"Error updating message on timeout: {e}")

    def get_current_page_results(self):
        """Get the results for the current page"""
        start_idx = self.current_page * self.results_per_page
        end_idx = start_idx + self.results_per_page
        return self.results[start_idx:end_idx]


class MatchSelectView(ui.View):
    """View for selecting a match to be reminded about"""
    def __init__(self, matches, user_id, channel_id):
        super().__init__(timeout=60)
        self.matches = matches
        self.user_id = user_id
        self.channel_id = channel_id

        # Add select menu with matches
        select_options = []
        for i, match in enumerate(matches):
            select_options.append(
                discord.SelectOption(
                    label=f"{match['team1']} vs {match['team2']}",
                    description=f"{match['time']} - {match['event']}",
                    value=str(i)
                )
            )

        self.select_menu = ui.Select(
            placeholder="Select a match...",
            options=select_options,
            min_values=1,
            max_values=1
        )
        self.select_menu.callback = self.select_callback
        self.add_item(self.select_menu)

    async def select_callback(self, interaction: discord.Interaction):
        """Callback for when a match is selected"""
        # Get the selected match
        match_index = int(self.select_menu.values[0])
        match = self.matches[match_index]

        # Parse the match time
        match_time = match['time']

        # Get server timezone
        server_id = str(interaction.guild_id)
        config = get_server_config(server_id)
        server_timezone = config['timezone']

        # Parse the match time
        parsed_time = parse_match_time(match_time, server_timezone)
        if parsed_time is None:
            await interaction.response.send_message(
                f"Could not parse match time: {match_time}. Please try another match.",
                ephemeral=True
            )
            return

        # Store the reminder in the database
        add_match_reminder(
            self.user_id,
            self.channel_id,
            match['url'],
            parsed_time.isoformat(),
            match['team1'],
            match['team2']
        )

        # Format the time in the user's timezone
        formatted_time = format_match_time(parsed_time, server_timezone)

        # Send confirmation message
        await interaction.response.send_message(
            f"You will be reminded about the match between **{match['team1']}** and **{match['team2']}** at {formatted_time}.",
            ephemeral=True
        )

        # Disable the select menu
        self.select_menu.disabled = True
        await interaction.edit_original_response(view=self)

    async def on_timeout(self):
        """Called when the view times out"""
        for item in self.children:
            item.disabled = True


async def create_match_results_embed(results, upcoming=False, page_info=None, team=None, tournament=None):
    """
    Creates a Discord embed for match results or upcoming matches

    Args:
        results: List of match result dictionaries
        upcoming: Whether these are upcoming matches or past results
        page_info: Tuple of (current_page, total_pages) for pagination
        team: Team name for team-specific results
        tournament: Tournament name for tournament-specific results

    Returns:
        discord.Embed: Formatted embed with match information
    """
    # Set up pagination info
    page_text = ""
    if page_info:
        current_page, total_pages = page_info
        page_text = f" (Page {current_page}/{total_pages})"

    # Determine the appropriate color based on context
    if tournament:
        color = get_entity_color(tournament, is_tournament=True)
        title = f"{tournament} Tournament Results"
        description = f"Results from {tournament} tournament{page_text}"
        url = "https://www.vlr.gg/matches/results"
    elif team:
        color = get_entity_color(team)
        title = f"{team} Match Results"
        description = f"Results for {team} from vlr.gg{page_text}"
        url = "https://www.vlr.gg/matches/results"
    elif upcoming:
        color = discord.Color.green()
        title = "Upcoming Valorant Matches"
        description = f"Next {len(results)} matches from vlr.gg{page_text}"
        url = "https://www.vlr.gg/matches"
    else:
        color = discord.Color.red()
        title = "Recent Valorant Match Results"
        description = f"Latest {len(results)} results from vlr.gg{page_text}"
        url = "https://www.vlr.gg/matches/results"

    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        url=url
    )

    # Add match results to embed
    for result in results:
        match_title = f"{result['team1']} vs {result['team2']}"

        if upcoming:
            # Format for upcoming matches
            match_time = result.get('time', 'TBD')
            match_value = (
                f"**When:** {match_time}\n"
                f"**Event:** {result['event']}\n"
                f"**Stage:** {result['stage']}\n"
                f"[Match Details]({result['url']})"
            )
        else:
            # Format for past results
            match_value = (
                f"**Score:** {result['score1']} - {result['score2']}\n"
                f"**Event:** {result['event']}\n"
                f"**Stage:** {result['stage']}\n"
                f"[Match Details]({result['url']})"
            )

        embed.add_field(name=match_title, value=match_value, inline=False)

    # Add timestamp
    embed.set_footer(text=f"Data from vlr.gg • {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    return embed

# Slash command for recent results
@bot.tree.command(name="results", description="Get recent Valorant match results")
@app_commands.describe(count="Number of results to display (5-20)")
async def slash_results(interaction: discord.Interaction, count: int = 5):
    """
    Slash command to fetch and display recent Valorant match results

    Args:
        interaction: Discord interaction
        count: Number of results to display (default: 5)
    """
    # Validate input
    if count < 5 or count > 20:
        await interaction.response.send_message("Please specify a number between 5 and 20.", ephemeral=True)
        return

    # Acknowledge the command
    await interaction.response.defer(thinking=True)

    # Get results
    results = await get_valorant_results(count)

    if results is None:
        await interaction.followup.send("❌ Error connecting to vlr.gg. The website might be down or experiencing issues. Please try again later.")
        return

    if not results:
        await interaction.followup.send("No match results found. The website structure might have changed or there might be no recent matches.")
        return

    # Create paginator view
    paginator = ResultsPaginator(results, upcoming=False)

    # Get first page of results
    first_page = paginator.get_current_page_results()

    # Create and send embed with pagination
    embed = await create_match_results_embed(
        first_page,
        upcoming=False,
        page_info=(1, paginator.total_pages) if paginator.total_pages > 1 else None
    )

    # Send the message with the paginator view
    if paginator.total_pages > 1:
        # Store the message in the paginator for timeout handling
        paginator.message = await interaction.followup.send(embed=embed, view=paginator)
    else:
        await interaction.followup.send(embed=embed)

# Slash command for upcoming matches
@bot.tree.command(name="upcoming", description="Get upcoming Valorant matches")
@app_commands.describe(count="Number of matches to display (5-20)")
async def slash_upcoming(interaction: discord.Interaction, count: int = 5):
    """
    Slash command to fetch and display upcoming Valorant matches

    Args:
        interaction: Discord interaction
        count: Number of matches to display (default: 5)
    """
    # Validate input
    if count < 5 or count > 20:
        await interaction.response.send_message("Please specify a number between 5 and 20.", ephemeral=True)
        return

    # Acknowledge the command
    await interaction.response.defer(thinking=True)

    # Get upcoming matches
    results = await get_valorant_results(count, upcoming=True)

    if results is None:
        await interaction.followup.send("❌ Error connecting to vlr.gg. The website might be down or experiencing issues. Please try again later.")
        return

    if not results:
        await interaction.followup.send("No upcoming matches found. The website structure might have changed or there might be no scheduled matches at the moment.")
        return

    # Create paginator view
    paginator = ResultsPaginator(results, upcoming=True)

    # Get first page of results
    first_page = paginator.get_current_page_results()

    # Create and send embed with pagination
    embed = await create_match_results_embed(
        first_page,
        upcoming=True,
        page_info=(1, paginator.total_pages) if paginator.total_pages > 1 else None
    )

    # Send the message with the paginator view
    if paginator.total_pages > 1:
        # Store the message in the paginator for timeout handling
        paginator.message = await interaction.followup.send(embed=embed, view=paginator)
    else:
        await interaction.followup.send(embed=embed)

# Slash command for team search
@bot.tree.command(name="team", description="Search for results from a specific team")
@app_commands.describe(
    team_name="Name of the team to search for",
    count="Number of results to display (5-20)"
)
async def slash_team(interaction: discord.Interaction, team_name: str, count: int = 5):
    """
    Slash command to search for results from a specific team

    Args:
        interaction: Discord interaction
        team_name: Name of the team to search for
        count: Number of results to display (default: 5)
    """
    # Validate input
    if count < 5 or count > 20:
        await interaction.response.send_message("Please specify a number between 5 and 20.", ephemeral=True)
        return

    if len(team_name) < 2:
        await interaction.response.send_message("Please enter a team name with at least 2 characters.", ephemeral=True)
        return

    # Acknowledge the command
    await interaction.response.defer(thinking=True)

    # Resolve team name using aliases and fuzzy matching
    resolved_team = resolve_team_name(team_name)
    if resolved_team != team_name:
        await interaction.followup.send(f"Searching for team '{resolved_team}' (resolved from '{team_name}')")

    # Get team results
    results = await get_valorant_results(count, upcoming=False, team=resolved_team)

    if results is None:
        await interaction.followup.send("❌ Error connecting to vlr.gg. The website might be down or experiencing issues. Please try again later.")
        return

    if not results:
        await interaction.followup.send(f"No match results found for team '{team_name}'. The team might not have played any recent matches, or you may need to try a different spelling (e.g., 'C9' instead of 'Cloud9').")
        return

    # Create paginator view
    paginator = ResultsPaginator(results, upcoming=False, team=team_name)

    # Get first page of results
    first_page = paginator.get_current_page_results()

    # Create and send embed with pagination
    embed = await create_match_results_embed(
        first_page,
        upcoming=False,
        page_info=(1, paginator.total_pages) if paginator.total_pages > 1 else None,
        team=team_name
    )

    # Send the message with the paginator view
    if paginator.total_pages > 1:
        # Store the message in the paginator for timeout handling
        paginator.message = await interaction.followup.send(embed=embed, view=paginator)
    else:
        await interaction.followup.send(embed=embed)

# Slash command for tournament search
@bot.tree.command(name="tournament", description="Search for results from a specific tournament")
@app_commands.describe(
    tournament_name="Name of the tournament to search for (e.g., VCT, Masters, Champions)",
    count="Number of results to display (5-20)"
)
async def slash_tournament(interaction: discord.Interaction, tournament_name: str, count: int = 5):
    """
    Slash command to search for results from a specific tournament

    Args:
        interaction: Discord interaction
        tournament_name: Name of the tournament to search for
        count: Number of results to display (default: 5)
    """
    # Validate input
    if count < 5 or count > 20:
        await interaction.response.send_message("Please specify a number between 5 and 20.", ephemeral=True)
        return

    if len(tournament_name) < 2:
        await interaction.response.send_message("Please enter a tournament name with at least 2 characters.", ephemeral=True)
        return

    # Acknowledge the command
    await interaction.response.defer(thinking=True)

    # Resolve tournament name using aliases and fuzzy matching
    resolved_tournament = resolve_tournament_name(tournament_name)
    if resolved_tournament != tournament_name:
        await interaction.followup.send(f"Searching for tournament '{resolved_tournament}' (resolved from '{tournament_name}')")

    # Get tournament results
    results = await get_valorant_results(count, upcoming=False, tournament=resolved_tournament)

    if results is None:
        await interaction.followup.send("❌ Error connecting to vlr.gg. The website might be down or experiencing issues. Please try again later.")
        return

    if not results:
        await interaction.followup.send(f"No match results found for tournament '{tournament_name}'. Try common tournament names like 'VCT', 'Masters', 'Champions', or 'Challengers'. The tournament might be spelled differently on vlr.gg.")
        return

    # Create paginator view
    paginator = ResultsPaginator(results, upcoming=False, tournament=tournament_name)

    # Get first page of results
    first_page = paginator.get_current_page_results()

    # Create and send embed with pagination
    embed = await create_match_results_embed(
        first_page,
        upcoming=False,
        page_info=(1, paginator.total_pages) if paginator.total_pages > 1 else None,
        tournament=tournament_name
    )

    # Send the message with the paginator view
    if paginator.total_pages > 1:
        # Store the message in the paginator for timeout handling
        paginator.message = await interaction.followup.send(embed=embed, view=paginator)
    else:
        await interaction.followup.send(embed=embed)

# Command to force sync slash commands (owner only)
@bot.tree.command(name="sync", description="Force sync slash commands with Discord (Owner only)")
async def sync_commands(interaction: discord.Interaction):
    """Owner-only command to force sync slash commands"""
    # Check if the user is the bot owner
    app_info = await bot.application_info()
    if interaction.user.id != app_info.owner.id:
        await interaction.response.send_message("This command can only be used by the bot owner.", ephemeral=True)
        return

    # Sync commands
    try:
        await interaction.response.defer(ephemeral=True)
        synced = await bot.tree.sync()
        await interaction.followup.send(f"Successfully synced {len(synced)} commands!", ephemeral=True)
        logger.info(f"Commands manually synced by owner: {len(synced)} commands")
    except Exception as e:
        await interaction.followup.send(f"Error syncing commands: {e}", ephemeral=True)
        logger.error(f"Error during manual command sync: {e}")

# Match details command
@bot.tree.command(name="match_details", description="Get detailed information about a specific match")
@app_commands.describe(match_url="URL of the match on vlr.gg")
async def match_details(interaction: discord.Interaction, match_url: str):
    """Slash command to get detailed information about a specific match"""
    # Validate input
    if not match_url or not (match_url.startswith("https://www.vlr.gg/") or match_url.startswith("https://vlr.gg/")):
        await interaction.response.send_message("Please provide a valid vlr.gg match URL.", ephemeral=True)
        return

    # Acknowledge the command
    await interaction.response.defer(thinking=True)

    # Get match details
    match_details = await get_match_details(match_url)

    if match_details is None:
        await interaction.followup.send("❌ Error fetching match details. The URL might be invalid or vlr.gg might be experiencing issues.")
        return

    # Create embed
    color = discord.Color.blue()
    team1_lower = match_details['team1'].lower()
    team2_lower = match_details['team2'].lower()

    # Try to get team colors
    for team, team_color in TEAM_COLORS.items():
        if team in team1_lower:
            color = discord.Color(team_color)
            break
        elif team in team2_lower:
            color = discord.Color(team_color)
            break

    embed = discord.Embed(
        title=f"{match_details['team1']} vs {match_details['team2']}",
        description=f"**Event:** {match_details['event']}\n**Stage:** {match_details['stage']}",
        color=color,
        url=match_details['url']
    )

    # Add overall score
    embed.add_field(
        name="Overall Score",
        value=f"**{match_details['team1']}** {match_details['score1']} - {match_details['score2']} **{match_details['team2']}**",
        inline=False
    )

    # Add match status/time
    if match_details['status']:
        embed.add_field(
            name="Status",
            value=match_details['status'],
            inline=True
        )

    if match_details['time']:
        embed.add_field(
            name="Time",
            value=match_details['time'],
            inline=True
        )

    # Add map details
    if match_details['maps']:
        maps_text = ""
        for map_info in match_details['maps']:
            maps_text += f"**{map_info['name']}**: {map_info['score1']} - {map_info['score2']}\n"

        embed.add_field(
            name="Maps",
            value=maps_text,
            inline=False
        )

    # Add timestamp
    embed.set_footer(text=f"Data from vlr.gg • {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    await interaction.followup.send(embed=embed)

# Server status command
@bot.tree.command(name="server_status", description="Check Valorant server status")
async def server_status(interaction: discord.Interaction):
    """Slash command to check Valorant server status"""
    await interaction.response.defer(thinking=True)

    try:
        # Fetch server status from Riot's status page
        async with aiohttp.ClientSession() as session:
            async with session.get("https://status.riotgames.com/valorant", timeout=10) as response:
                if response.status != 200:
                    await interaction.followup.send("❌ Error fetching server status. Please check https://status.riotgames.com/valorant manually.")
                    return

                # Create embed
                embed = discord.Embed(
                    title="Valorant Server Status",
                    description="Current status of Valorant servers",
                    color=discord.Color.green(),
                    url="https://status.riotgames.com/valorant"
                )

                embed.add_field(
                    name="Status Page",
                    value="[Check Valorant Status Page](https://status.riotgames.com/valorant)",
                    inline=False
                )

                embed.add_field(
                    name="Riot Support",
                    value="[Contact Riot Support](https://support-valorant.riotgames.com/)",
                    inline=False
                )

                embed.set_footer(text=f"Data from Riot Games • {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

                await interaction.followup.send(embed=embed)
    except Exception as e:
        logger.error(f"Error fetching server status: {e}")
        await interaction.followup.send(f"❌ Error fetching server status: {e}. Please check https://status.riotgames.com/valorant manually.")

# Random agent command
@bot.tree.command(name="random_agent", description="Get a random Valorant agent")
async def random_agent(interaction: discord.Interaction):
    """Slash command to get a random Valorant agent"""
    VALORANT_AGENTS = [
        "Astra", "Breach", "Brimstone", "Chamber", "Cypher", "Deadlock", "Fade", "Gekko",
        "Harbor", "Jett", "KAY/O", "Killjoy", "Neon", "Omen", "Phoenix", "Raze", "Reyna",
        "Sage", "Skye", "Sova", "Viper", "Yoru", "Iso", "Clove"
    ]

    agent = random.choice(VALORANT_AGENTS)

    embed = discord.Embed(
        title="Random Agent",
        description=f"Your random agent is: **{agent}**",
        color=discord.Color.blue()
    )

    # Try to add agent image if available
    embed.set_thumbnail(url=f"https://valorant-api.com/v1/agents?name={agent}")

    await interaction.response.send_message(embed=embed)

# Random map command
@bot.tree.command(name="random_map", description="Get a random Valorant map")
async def random_map(interaction: discord.Interaction):
    """Slash command to get a random Valorant map"""
    VALORANT_MAPS = [
        "Ascent", "Bind", "Breeze", "Fracture", "Haven", "Icebox", "Lotus", "Pearl", "Split", "Sunset"
    ]

    map_name = random.choice(VALORANT_MAPS)

    embed = discord.Embed(
        title="Random Map",
        description=f"Your random map is: **{map_name}**",
        color=discord.Color.green()
    )

    await interaction.response.send_message(embed=embed)

# Server configuration command
@bot.tree.command(name="config", description="Configure server settings for the bot")
@app_commands.describe(
    setting="Setting to configure (default_count, timezone, announcement_channel)",
    value="Value to set for the setting"
)
async def server_config(interaction: discord.Interaction, setting: str, value: str = None):
    """Slash command to configure server settings"""
    # Check if user has manage server permissions
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("You need 'Manage Server' permissions to use this command.", ephemeral=True)
        return

    # Get server ID
    server_id = str(interaction.guild_id)

    # If no value provided, show current setting
    if value is None:
        config = get_server_config(server_id)

        if setting.lower() == "default_count":
            await interaction.response.send_message(f"Current default count: {config['default_count']}", ephemeral=True)
        elif setting.lower() == "timezone":
            await interaction.response.send_message(f"Current timezone: {config['timezone']}", ephemeral=True)
        elif setting.lower() == "announcement_channel":
            channel_id = config['announcement_channel']
            channel_name = "None" if channel_id is None else f"<#{channel_id}>"
            await interaction.response.send_message(f"Current announcement channel: {channel_name}", ephemeral=True)
        else:
            await interaction.response.send_message(
                "Available settings:\n" +
                "- `default_count`: Default number of results to display (5-20)\n" +
                "- `timezone`: Server timezone (e.g., 'UTC', 'US/Eastern')\n" +
                "- `announcement_channel`: Channel for match announcements",
                ephemeral=True
            )
        return

    # Update setting
    if setting.lower() == "default_count":
        try:
            count = int(value)
            if count < 5 or count > 20:
                await interaction.response.send_message("Default count must be between 5 and 20.", ephemeral=True)
                return

            update_server_config(server_id, "default_count", count)
            await interaction.response.send_message(f"Default count set to {count}.", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("Please provide a valid number for default_count.", ephemeral=True)

    elif setting.lower() == "timezone":
        if not is_valid_timezone(value):
            # Show some common timezones
            common_timezones = get_timezone_list()
            await interaction.response.send_message(
                f"Invalid timezone: '{value}'. Please use a valid timezone identifier.\n" +
                f"Common timezones: {', '.join(common_timezones[:10])}...",
                ephemeral=True
            )
            return

        update_server_config(server_id, "timezone", value)
        await interaction.response.send_message(f"Timezone set to {value}.", ephemeral=True)

    elif setting.lower() == "announcement_channel":
        if value.lower() == "none":
            update_server_config(server_id, "announcement_channel", None)
            await interaction.response.send_message("Announcement channel cleared.", ephemeral=True)
            return

        # Try to get channel ID from mention
        channel_id = value.strip()
        if channel_id.startswith("<#") and channel_id.endswith(">"):
            # Extract the numeric ID from the mention
            channel_id = channel_id[2:-1]

        # Check if the channel exists
        channel = interaction.guild.get_channel(int(channel_id)) if channel_id.isdigit() else None
        if channel is None:
            await interaction.response.send_message(f"Channel not found. Please provide a valid channel mention or ID.", ephemeral=True)
            return

        update_server_config(server_id, "announcement_channel", channel_id)
        await interaction.response.send_message(f"Announcement channel set to {channel.mention}.", ephemeral=True)
    else:
        await interaction.response.send_message(
            "Unknown setting. Available settings:\n" +
            "- `default_count`: Default number of results to display (5-20)\n" +
            "- `timezone`: Server timezone (e.g., 'UTC', 'US/Eastern')\n" +
            "- `announcement_channel`: Channel for match announcements",
            ephemeral=True
        )

# Help command
@bot.tree.command(name="help", description="Show bot commands and information")
async def slash_help(interaction: discord.Interaction):
    """Slash command to display help information"""
    embed = discord.Embed(
        title="VCT Results Bot Help",
        description="Get the latest Valorant Champions Tour match results directly in Discord!",
        color=discord.Color.blue()
    )

    # Commands section
    embed.add_field(
        name="📋 Match Commands",
        value=(
            "`/results [count]` - Get recent match results\n"
            "`/upcoming [count]` - Get upcoming matches\n"
            "`/team [team_name] [count]` - Search for a specific team\n"
            "`/tournament [tournament_name] [count]` - Search for a specific tournament\n"
            "`/match_details [match_url]` - Get detailed information about a specific match\n"
        ),
        inline=False
    )

    # Utility commands section
    embed.add_field(
        name="🔧 Utility Commands",
        value=(
            "`/random_agent` - Get a random Valorant agent\n"
            "`/random_map` - Get a random Valorant map\n"
            "`/server_status` - Check Valorant server status\n"
            "`/help` - Show this help message\n"
        ),
        inline=False
    )

    # Configuration commands section
    embed.add_field(
        name="⚙️ Configuration Commands",
        value=(
            "`/config default_count [value]` - Set default number of results\n"
            "`/config timezone [value]` - Set server timezone\n"
            "`/config announcement_channel [value]` - Set announcement channel\n"
        ),
        inline=False
    )

    # Parameters section
    embed.add_field(
        name="⚙️ Parameters",
        value=(
            "`count` - Number of results to display (5-20, default: 5)\n"
            "`team_name` - Name of the team to search for (min 2 characters)\n"
            "`tournament_name` - Name of the tournament to search for (min 2 characters)\n"
        ),
        inline=False
    )

    # Examples section
    embed.add_field(
        name="💡 Examples",
        value=(
            "`/results` - Show 5 most recent results\n"
            "`/results 10` - Show 10 recent results with pagination\n"
            "`/upcoming` - Show 5 upcoming matches\n"
            "`/upcoming 15` - Show 15 upcoming matches with pagination\n"
            "`/team sentinels` - Show 5 results for Sentinels\n"
            "`/team c9 20` - Show 20 results for Cloud9 with team alias\n"
            "`/tournament vct` - Show 5 results from VCT tournaments\n"
            "`/tournament masters 10` - Show 10 results from Masters tournaments\n"
            "`/match_details https://www.vlr.gg/123456` - Get detailed info for a match\n"
            "`/random_agent` - Get a random Valorant agent\n"
            "`/random_map` - Get a random Valorant map\n"
            "`/server_status` - Check Valorant server status\n"
            "`/config timezone US/Eastern` - Set server timezone to US/Eastern\n"
        ),
        inline=False
    )

    # Add bot info
    embed.set_footer(text="Data sourced from vlr.gg • Bot created by lkukec22")

    await interaction.response.send_message(embed=embed, ephemeral=True)


# Database setup
DB_PATH = 'valorant_bot.db'

def init_database():
    """Initialize the SQLite database with required tables"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Create server configurations table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS server_configs (
        server_id TEXT PRIMARY KEY,
        default_count INTEGER DEFAULT 5,
        timezone TEXT DEFAULT 'UTC',
        announcement_channel TEXT
    )
    ''')

    # Create match reminders table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS match_reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        match_url TEXT NOT NULL,
        match_time TEXT NOT NULL,
        team1 TEXT NOT NULL,
        team2 TEXT NOT NULL,
        reminded BOOLEAN DEFAULT FALSE,
        created_at TEXT NOT NULL
    )
    ''')

    conn.commit()
    conn.close()
    logger.info("Database initialized successfully")

# Initialize database on startup
init_database()

# Database helper functions
def get_server_config(server_id):
    """Get server configuration from database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM server_configs WHERE server_id = ?', (str(server_id),))
    result = cursor.fetchone()

    conn.close()

    if result:
        return {
            'server_id': result[0],
            'default_count': result[1],
            'timezone': result[2],
            'announcement_channel': result[3]
        }
    else:
        # Return default config if not found
        return {
            'server_id': str(server_id),
            'default_count': 5,
            'timezone': 'UTC',
            'announcement_channel': None
        }

def update_server_config(server_id, setting, value):
    """Update server configuration in database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Check if server config exists
    cursor.execute('SELECT * FROM server_configs WHERE server_id = ?', (str(server_id),))
    result = cursor.fetchone()

    if result:
        # Update existing config
        cursor.execute(f'UPDATE server_configs SET {setting} = ? WHERE server_id = ?', (value, str(server_id)))
    else:
        # Create new config with defaults and the specified setting
        default_count = 5
        timezone = 'UTC'
        announcement_channel = None

        if setting == 'default_count':
            default_count = value
        elif setting == 'timezone':
            timezone = value
        elif setting == 'announcement_channel':
            announcement_channel = value

        cursor.execute(
            'INSERT INTO server_configs (server_id, default_count, timezone, announcement_channel) VALUES (?, ?, ?, ?)',
            (str(server_id), default_count, timezone, announcement_channel)
        )

    conn.commit()
    conn.close()
    return True

def add_match_reminder(user_id, channel_id, match_url, match_time, team1, team2):
    """Add a match reminder to the database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    created_at = datetime.datetime.now().isoformat()

    cursor.execute(
        'INSERT INTO match_reminders (user_id, channel_id, match_url, match_time, team1, team2, reminded, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (str(user_id), str(channel_id), match_url, match_time, team1, team2, False, created_at)
    )

    reminder_id = cursor.lastrowid

    conn.commit()
    conn.close()

    return reminder_id

def get_pending_reminders():
    """Get all pending match reminders"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM match_reminders WHERE reminded = 0')
    results = cursor.fetchall()

    reminders = []
    for result in results:
        reminders.append({
            'id': result[0],
            'user_id': result[1],
            'channel_id': result[2],
            'match_url': result[3],
            'match_time': result[4],
            'team1': result[5],
            'team2': result[6],
            'reminded': bool(result[7]),
            'created_at': result[8]
        })

    conn.close()
    return reminders

def mark_reminder_as_sent(reminder_id):
    """Mark a reminder as sent"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('UPDATE match_reminders SET reminded = 1 WHERE id = ?', (reminder_id,))

    conn.commit()
    conn.close()
    return True

# Track the last time the server was pinged
last_ping_time = time.time()

class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global last_ping_time
        last_ping_time = time.time()

        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(b'Bot is running!')
        logger.info(f"Server pinged at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

def run_server():
    port = int(os.environ.get('PORT', 8080))
    server_address = ('', port)
    httpd = HTTPServer(server_address, SimpleHTTPRequestHandler)
    logger.info(f'Starting web server on port {port}')
    httpd.serve_forever()

# Task to keep the server alive by self-pinging
@tasks.loop(minutes=10)
async def keep_alive():
    """Ping our own web server to prevent Render from putting it to sleep"""
    try:
        # Get the server URL from environment or use localhost for development
        server_url = os.environ.get('SERVER_URL')
        if not server_url:
            # If SERVER_URL is not set, try to use RENDER_EXTERNAL_URL (provided by Render)
            server_url = os.environ.get('RENDER_EXTERNAL_URL')

        # If still no URL, use localhost (for development)
        if not server_url:
            server_url = f"http://localhost:{os.environ.get('PORT', 8080)}"

        # Only ping if it's been more than 10 minutes since the last ping
        if time.time() - last_ping_time > 600:  # 600 seconds = 10 minutes
            logger.info(f"Self-pinging server at {server_url} to keep alive")
            async with aiohttp.ClientSession() as session:
                async with session.get(server_url, timeout=10) as response:
                    if response.status == 200:
                        logger.info("Keep-alive ping successful")
                    else:
                        logger.warning(f"Keep-alive ping returned status {response.status}")
    except Exception as e:
        logger.error(f"Error in keep-alive ping: {e}")

@keep_alive.before_loop
async def before_keep_alive():
    await bot.wait_until_ready()

if __name__ == "__main__":
    server_thread = threading.Thread(target=run_server)
    server_thread.daemon = True
    server_thread.start()

    bot.run(TOKEN)
