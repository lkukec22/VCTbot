import discord
from discord.ext import commands
import os
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import logging
import datetime
import threading
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
intents.message_content = True  # Required to read message content for commands

# Create bot instance
bot = commands.Bot(command_prefix='!', intents=intents)

# Cache for storing results to avoid frequent requests
results_cache = {
    'data': None,
    'timestamp': None
}

async def get_valorant_results(limit=5):
    """
    Scrapes vlr.gg for recent Valorant match results.

    Args:
        limit (int): Maximum number of results to return

    Returns:
        list: List of formatted match results or None if an error occurred
    """
    # Check cache first (valid for 5 minutes)
    current_time = datetime.datetime.now()
    if (results_cache['data'] is not None and results_cache['timestamp'] is not None and
            (current_time - results_cache['timestamp']).total_seconds() < 300):
        logger.info("Using cached results")
        return results_cache['data'][:limit]

    url = "https://www.vlr.gg/matches/results"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    try:
        logger.info(f"Fetching match results from {url}")
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')

        # Find match containers - updated selector for current vlr.gg structure
        # Looking for match result entries which are <a> tags containing match info
        match_elements = []

        # First, find all date headers (they're not inside the match elements)
        date_headers = soup.find_all(lambda tag: tag.name == 'div' and tag.text.strip().endswith('Today') or
                                              tag.text.strip().endswith('Yesterday'))

        # For each date header, find the following match elements until the next date header
        for header in date_headers[:2]:  # Only look at Today and Yesterday for recent matches
            # Get the next elements after the date header
            current = header.next_sibling

            while current and (not isinstance(current, type(header)) or
                              not (current.text.strip().endswith('Today') or
                                   current.text.strip().endswith('Yesterday'))):
                if current.name == 'a' and current.get('href', '').startswith('/'):
                    match_elements.append(current)
                current = current.next_sibling
                if not current:
                    break

        if not match_elements:
            # Fallback method - try to find all match elements directly
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
                # The structure is different now - team names are direct text nodes
                # Extract all text nodes and filter
                all_text = [text for text in match.stripped_strings]

                # Log the text content for debugging
                logger.info(f"Match text content: {all_text}")

                # Team names and scores are in specific positions
                # Based on the HTML structure we observed
                team1 = "Unknown"
                team2 = "Unknown"
                score1 = "?"
                score2 = "?"

                # Try different patterns to extract team names and scores
                if len(all_text) >= 6:
                    # Pattern: [time, team1, score1, team2, score2, status, ...]
                    if all_text[0].count(':') == 1 and all_text[0].count(' ') <= 1:  # Looks like a time
                        team1 = all_text[1]
                        score1 = all_text[2]
                        team2 = all_text[3]
                        score2 = all_text[4]
                    # Another common pattern
                    elif len(all_text) >= 10 and all_text[2].isdigit() and all_text[4].isdigit():
                        team1 = all_text[1]
                        score1 = all_text[2]
                        team2 = all_text[3]
                        score2 = all_text[4]

                # Extract event info
                event_name = "Unknown Event"
                event_stage = ""

                # Event info is typically after "Completed" text
                completed_index = -1
                for i, text in enumerate(all_text):
                    if text == "Completed":
                        completed_index = i
                        break

                if completed_index != -1 and completed_index + 2 < len(all_text):
                    # Event info is typically 2 elements after "Completed"
                    event_text = all_text[completed_index + 2]
                    if '–' in event_text:
                        event_parts = event_text.split('–')
                        event_stage = event_parts[0].strip()
                        event_name = event_parts[1].strip()
                    else:
                        event_name = event_text.strip()

                    # The next element is usually the tournament name
                    if completed_index + 3 < len(all_text):
                        event_name = all_text[completed_index + 3]

                # Format result
                result = {
                    'team1': team1,
                    'team2': team2,
                    'score1': score1,
                    'score2': score2,
                    'event': event_name,
                    'stage': event_stage,
                    'url': f"https://www.vlr.gg{match.get('href', '')}"
                }

                results.append(result)
            except Exception as e:
                logger.error(f"Error parsing match element: {e}")

        # Update cache
        results_cache['data'] = results
        results_cache['timestamp'] = current_time

        return results

    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching data: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return None

@bot.event
async def on_ready():
    """Event triggered when the bot is ready"""
    logger.info(f'Logged in as {bot.user.name} (ID: {bot.user.id})')
    logger.info('Bot is ready!')

    # Set bot status
    await bot.change_presence(activity=discord.Game(name="!vlr for match results"))

@bot.command(name='vlr', help='Get recent Valorant match results')
async def valorant_results(ctx, count: int = 5):
    """
    Command to fetch and display recent Valorant match results

    Args:
        ctx: Command context
        count: Number of results to display (default: 5)
    """
    # Validate input
    if count < 1 or count > 10:
        await ctx.send("Please specify a number between 1 and 10.")
        return

    # Send initial message
    message = await ctx.send(f"Fetching the latest {count} Valorant match results...")

    # Get results
    results = await get_valorant_results(count)

    if results is None:
        await message.edit(content="❌ Error fetching match results. Please try again later.")
        return

    if not results:
        await message.edit(content="No match results found. The website structure might have changed.")
        return

    # Create embed
    embed = discord.Embed(
        title="Recent Valorant Match Results",
        description=f"Latest {len(results)} results from vlr.gg",
        color=discord.Color.red(),
        url="https://www.vlr.gg/matches/results"
    )

    # Add match results to embed
    for i, result in enumerate(results, 1):
        match_title = f"{result['team1']} vs {result['team2']}"
        match_value = (
            f"**Score:** {result['score1']} - {result['score2']}\n"
            f"**Event:** {result['event']}\n"
            f"**Stage:** {result['stage']}\n"
            f"[Match Details]({result['url']})"
        )
        embed.add_field(name=match_title, value=match_value, inline=False)

    # Add timestamp
    embed.set_footer(text=f"Data from vlr.gg • {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Send embed
    await message.edit(content=None, embed=embed)

@bot.event
async def on_command_error(ctx, error):
    """Global error handler for bot commands"""
    if isinstance(error, commands.CommandNotFound):
        return
    elif isinstance(error, commands.BadArgument):
        await ctx.send("Invalid argument. Please check the command syntax.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Missing required argument. Please check the command syntax.")
    else:
        logger.error(f"Command error: {error}")
        await ctx.send(f"An error occurred: {error}")

# Simple HTTP server to keep Render happy
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(b'Bot is running!')

def run_server():
    # Get port from environment variable or use default
    port = int(os.environ.get('PORT', 8080))
    server_address = ('', port)
    httpd = HTTPServer(server_address, SimpleHTTPRequestHandler)
    logger.info(f'Starting web server on port {port}')
    httpd.serve_forever()

# Run the bot
if __name__ == "__main__":
    # Start web server in a separate thread
    server_thread = threading.Thread(target=run_server)
    server_thread.daemon = True  # Thread will close when main program exits
    server_thread.start()

    # Run the Discord bot
    bot.run(TOKEN)
