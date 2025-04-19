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

# Tournament colors
TOURNAMENT_COLORS = {
    'vct': 0xFF4500,
    'masters': 0x9370DB,
    'champions': 0xFFD700,
    'ascension': 0x32CD32,
    'game changers': 0xFF69B4,
    'challengers': 0x4169E1,
}

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

    # Get team results
    results = await get_valorant_results(count, upcoming=False, team=team_name)

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

    # Get tournament results
    results = await get_valorant_results(count, upcoming=False, tournament=tournament_name)

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
        name="📋 Commands",
        value=(
            "`/results [count]` - Get recent match results\n"
            "`/upcoming [count]` - Get upcoming matches\n"
            "`/team [team_name] [count]` - Search for a specific team\n"
            "`/tournament [tournament_name] [count]` - Search for a specific tournament\n"
            "`/help` - Show this help message\n"
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
            "`/team cloud9 20` - Show 20 results for Cloud9 with pagination\n"
            "`/tournament vct` - Show 5 results from VCT tournaments\n"
            "`/tournament masters 10` - Show 10 results from Masters tournaments\n"
        ),
        inline=False
    )

    # Add bot info
    embed.set_footer(text="Data sourced from vlr.gg • Bot created by lkukec22")

    await interaction.response.send_message(embed=embed, ephemeral=True)


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
