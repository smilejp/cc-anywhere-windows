"""Interface adapters: web terminal, messenger bots."""

from .discord_bot import CCBot, run_bot as run_discord_bot
from .slack_bot import SlackBot, run_bot as run_slack_bot
from .telegram_bot import TelegramBot, run_bot as run_telegram_bot
from .web import app, set_session_manager

__all__ = [
    # Web
    "app",
    "set_session_manager",
    # Discord
    "CCBot",
    "run_discord_bot",
    # Telegram
    "TelegramBot",
    "run_telegram_bot",
    # Slack
    "SlackBot",
    "run_slack_bot",
]
