# Valorant Results Discord Bot

A Discord bot that fetches and displays Valorant match results, upcoming matches, team-specific results, and tournament information from vlr.gg.

## Features

- **Match Results**: Get the latest Valorant match results
- **Upcoming Matches**: View scheduled upcoming matches with times
- **Team Search**: Find results for specific teams
- **Tournament Filter**: Filter results by tournament (VCT, Masters, Champions, etc.)
- **Pagination**: Browse through multiple pages of results
- **Team Colors**: Automatic team-specific colors for popular Valorant teams
- **Robust Scraping**: Multi-layered approach to handle website changes
- **Health Monitoring**: Automatic alerts if scraping issues are detected

## Add the Bot to Your Server

The bot is hosted on Render and is ready to use! You can add it to your Discord server using this link:

[Add VCT Results Bot to Your Server](https://discord.com/oauth2/authorize?client_id=1363093726699978892&permissions=83968&integration_type=0&scope=bot)

## Usage

The bot uses Discord's slash commands for all functionality. Here are the available commands:

### Main Commands

- `/results [count]` - Get recent match results
- `/upcoming [count]` - Get upcoming matches
- `/team [team_name] [count]` - Search for a specific team
- `/tournament [tournament_name] [count]` - Search for a specific tournament
- `/help` - Show help information

### Parameters

- `count` - Number of results to display (5-20, default: 5)
- `team_name` - Name of the team to search for (min 2 characters)
- `tournament_name` - Name of the tournament to search for (min 2 characters)

### Examples

- `/results` - Show 5 most recent results
- `/results 10` - Show 10 recent results with pagination
- `/upcoming` - Show 5 upcoming matches
- `/team sentinels` - Show 5 results for Sentinels
- `/tournament vct` - Show 5 results from VCT tournaments

## Technical Details

- **Asynchronous Design**: Uses aiohttp for non-blocking web requests
- **Caching System**: Reduces load on vlr.gg and improves response times
- **Error Handling**: Comprehensive error handling for network and parsing issues
- **Health Monitoring**: Periodic checks to ensure scraping functionality

## Disclaimer

This bot scrapes data from vlr.gg. Web scraping can be fragile and may break if the website structure changes. This bot is for educational purposes only and is not affiliated with Riot Games or vlr.gg.

## License

MIT
