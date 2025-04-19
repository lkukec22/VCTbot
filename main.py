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
        match_elements = soup.select('a[href^="/"]')

        # Filter to only match result elements
        match_elements = [m for m in match_elements if m.select('.match-item-vs') and m.get('href', '').startswith('/')]

        if not match_elements:
            logger.warning("No match elements found. Website structure might have changed.")
            return []

        logger.info(f"Found {len(match_elements)} match elements")

        results = []
        for match in match_elements[:limit]:
            try:
                # Extract team names
                team_elements = match.select('.mod-1, .mod-2')
                team1 = team_elements[0].text.strip() if len(team_elements) > 0 else "Unknown"
                team2 = team_elements[1].text.strip() if len(team_elements) > 1 else "Unknown"

                # Extract scores
                score_elements = match.select('.match-item-vs-score')
                score1 = score_elements[0].text.strip() if len(score_elements) > 0 else "?"
                score2 = score_elements[1].text.strip() if len(score_elements) > 1 else "?"

                # Extract event info
                event_name = "Unknown Event"
                event_stage = ""

                # Try to get event info from different possible elements
                event_element = match.select_one('.match-item-event')
                if event_element:
                    event_parts = event_element.text.strip().split('–')
                    if len(event_parts) > 1:
                        event_stage = event_parts[0].strip()
                        event_name = event_parts[1].strip()
                    else:
                        event_name = event_parts[0].strip()

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
