# Valorant Results Discord Bot

A Discord bot that fetches and displays recent Valorant match results from vlr.gg.

## Features

- Fetches recent Valorant match results from vlr.gg
- Displays results in a nicely formatted embed
- Caches results to reduce API calls
- Simple command interface

## Setup

### Prerequisites

- Python 3.8 or higher
- A Discord account and a Discord server where you have permission to add bots
- A Discord bot token (see below)

### Creating a Discord Bot

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
2. Click "New Application" and give it a name
3. Go to the "Bot" tab and click "Add Bot"
4. Under the "Privileged Gateway Intents" section, enable "Message Content Intent"
5. Copy your bot token (you'll need this later)

### Inviting the Bot to Your Server

1. In the Discord Developer Portal, go to the "OAuth2" tab
2. In the "URL Generator" section, select the following scopes:
   - bot
3. Select the following bot permissions:
   - Send Messages
   - Embed Links
   - Read Message History
4. Copy the generated URL and open it in your browser
5. Select the server you want to add the bot to and click "Authorize"

### Installation

1. Clone this repository:
   ```
   git clone <repository-url>
   cd VCT-results-discord-bot
   ```

2. Create a virtual environment:
   ```
   python -m venv venv
   ```

3. Activate the virtual environment:
   - Windows: `venv\Scripts\activate`
   - macOS/Linux: `source venv/bin/activate`

4. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

5. Create a `.env` file in the project root and add your Discord bot token:
   ```
   DISCORD_TOKEN=your_discord_bot_token_here
   ```

## Usage

1. Start the bot:
   ```
   python main.py
   ```

2. In your Discord server, use the following command:
   ```
   !vlr [count]
   ```
   Where `[count]` is an optional number of results to display (default: 5, max: 10)

## Disclaimer

This bot scrapes data from vlr.gg. Web scraping can be fragile and may break if the website structure changes. This bot is for educational purposes only and is not affiliated with Riot Games or vlr.gg.

## License

MIT
