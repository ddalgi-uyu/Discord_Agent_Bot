"""Discord Bot entry point for the Intelligence Hub.

This bot provides an interactive interface to the existing agent services,
allowing users to trigger reports and manage configurations via slash commands.
"""
import asyncio
import logging
import os
import sys
from pathlib import Path

# Project root setup
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

import discord
from discord.ext import commands
from dotenv import load_dotenv

from common.config_loader import load_config
from common.discord_notifier import BotNotifier
from common.logging_setup import get_logger, setup_logging
from common.database import Database

# Import agents
from currency_notifier.config import CurrencyNotifierConfig
from currency_notifier.pipeline import run as currency_run
from etf_signal.config import EtfSignalConfig
from etf_signal.pipeline import run as etf_run
from news_digest.config import NewsDigestConfig
from news_digest.pipeline import run as news_run
from common.heartbeat import send_heartbeat

# Load environment variables
load_dotenv()

# Initialize logging
setup_logging(level="INFO", json=False, app_name="intelligence-bot")
logger = get_logger("bot")

class IntelligenceBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(
            command_prefix="!", 
            intents=intents,
            help_command=None
        )
        self.configs = {}
        self.db = None

    async def setup_hook(self):
        """Load configurations and database on startup."""
        logger.info("Initializing bot configurations...")
        
        # Load configs from YAML
        config_dir = _PROJECT_ROOT / "config"
        self.configs['news'] = load_config("news-digest", config_dir / "news_digest.yaml", config_class=NewsDigestConfig)
        self.configs['etf'] = load_config("etf-signal", config_dir / "etf_signal.yaml", config_class=EtfSignalConfig)
        self.configs['currency'] = load_config("currency-notifier", config_dir / "currency_notifier.yaml", config_class=CurrencyNotifierConfig)
        
        # Set up shared database
        self.db = Database(_PROJECT_ROOT / "data" / "currency_notifier.db")
        
        # Sync slash commands to Discord
        await self.tree.sync()
        logger.info("Slash commands synced to Discord.")

    async def on_ready(self):
        logger.info(f"Logged in as {self.user} (ID: {self.user.id})")
        logger.info("Intelligence Hub Bot is online and listening.")

bot = IntelligenceBot()

@bot.tree.command(name="status", description="Get a real-time technical analysis for an ETF symbol")
async def status(interaction: discord.Interaction, symbol: str):
    await interaction.response.defer()
    symbol = symbol.upper()
    
    try:
        # We use the etf_run pipeline but we only care about the specific symbol.
        # To avoid running the whole watchlist, we create a temporary config.
        from etf_signal.config import EtfSignalConfig
        temp_cfg = bot.configs['etf'].model_copy(deep=True)
        temp_cfg.symbols = [symbol]
        
        # Run pipeline for this specific symbol
        from common.database import Database
        db = Database(_PROJECT_ROOT / "data" / "etf_signal.db")
        try:
            # Use a temporary BotNotifier to leverage the bot's connection
            # But since we just want the embed for the command, we build it manually
            from etf_signal.pipeline import compute_indicators, analyze_sentiment, evaluate_signal, build_embed
            
            indicators = await compute_indicators(symbol, temp_cfg)
            if indicators is None:
                await interaction.followup.send(f"❌ Could not fetch data for `{symbol}`. Please check the symbol.")
                return

            sentiment = await analyze_sentiment(symbol, temp_cfg)
            signal = evaluate_signal(indicators, sentiment, temp_cfg)
            
            if signal is None:
                # No a lert signal, but we still show the current state (Neutral)
                label, reason = "NEUTRAL", "No strong buy signal detected at this time."
            else:
                label, reason = signal
            
            embed = build_embed(symbol, indicators, sentiment, label, reason, temp_cfg)
            await interaction.followup.send(embed=embed)
            logger.info(f"Status report for {symbol} sent to {interaction.user}")
            
        finally:
            db.close()

    except Exception as e:
        logger.exception(f"Error fetching status for {symbol}")
        await interaction.followup.send(f"❌ An error occurred while analyzing `{symbol}`: {e}")

@bot.tree.command(name="add_feed", description="Add a new RSS feed to the news digest")
async def add_feed(interaction: discord.Interaction, name: str, url: str, category: str):
    await interaction.response.defer()
    
    try:
        from common.config_manager import ConfigManager
        from news_digest.config import FeedConfig
        
        config_path = _PROJECT_ROOT / "config" / "news_digest.yaml"
        manager = ConfigManager(config_path)
        
        # Load current feeds
        current_config = manager.load()
        feeds = current_config.get("feeds", [])
        
        # Avoid duplicates
        if any(f.get("url") == url for f in feeds):
            await interaction.followup.send(f"Feed `{url}` is already in the watchlist.")
            return

        # Add new feed
        new_feed = FeedConfig(name=name, url=url, category=category).model_dump()
        feeds.append(new_feed)
        
        # Update config
        current_config["feeds"] = feeds
        manager.save(current_config)
        
        await interaction.followup.send(f"✅ Successfully added feed `{name}` to category `{category}`!")
        logger.info(f"User {interaction.user} added feed {url}")

    except Exception as e:
        logger.exception("Error adding feed")
        await interaction.followup.send(f"❌ Failed to add feed: {e}")

@bot.tree.command(name="heartbeat", description="Trigger an immediate Daily Market Heartbeat report")
async def heartbeat(interaction: discord.Interaction):
    await interaction.response.defer()
    
    try:
        # Run agents in 'data-only' mode
        currency_data = await currency_run(bot.configs['currency'], bot.db, return_all_data=True)
        etf_data = await etf_run(bot.configs['etf'], return_all_data=True)
        
        # News digest usually returns a string
        news_digest = await news_run(bot.configs['news'])
        
        from common.heartbeat import build_heartbeat_embed
        heartbeat_embed = await build_heartbeat_embed(
            currency_data=currency_data or [],
            etf_data=etf_data or [],
            news_digest=news_digest or "",
            discord_config=bot.configs['news'].discord
        )
        
        await interaction.followup.send(embed=heartbeat_embed)
        logger.info(f"Heartbeat report sent to channel {interaction.channel_id}")

    except Exception as e:
        logger.exception("Error generating heartbeat")
        await interaction.followup.send(f"❌ Failed to generate heartbeat: {e}")

if __name__ == "__main__":
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        logger.error("DISCORD_BOT_TOKEN not found in environment variables!")
        sys.exit(1)
        
    bot.run(token)
