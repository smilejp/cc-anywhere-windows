"""Discord Bot for CC-Anywhere."""

import asyncio
import logging
import re
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from ..core import (
    SessionAlreadyExistsError,
    SessionLimitError,
    SessionManager,
    SessionNotFoundError,
    create_formatter,
)
from ..core.event_bus import get_event_bus
from ..core.events import HookEvent
from ..core.summarizer import OutputSummarizer

logger = logging.getLogger(__name__)

# Maximum message length for Discord
MAX_MESSAGE_LENGTH = 1990  # Leave room for code blocks

# ANSI escape sequence pattern
ANSI_ESCAPE_PATTERN = re.compile(
    r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[PX^_][^\x1b]*\x1b\\"
)


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text.

    Args:
        text: Text potentially containing ANSI codes

    Returns:
        Clean text without ANSI sequences
    """
    return ANSI_ESCAPE_PATTERN.sub("", text)


class CCBot(commands.Bot):
    """CC-Anywhere Discord Bot."""

    def __init__(
        self,
        session_manager: SessionManager,
        allowed_user_ids: Optional[list[int]] = None,
        allowed_channel_ids: Optional[list[int]] = None,
        command_prefix: str = "/cc",
    ):
        """Initialize the bot.

        Args:
            session_manager: SessionManager instance
            allowed_user_ids: List of allowed Discord user IDs (None = all)
            allowed_channel_ids: List of channel IDs where direct messages work without /
            command_prefix: Command prefix
        """
        intents = discord.Intents.default()
        intents.message_content = True

        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
        )

        self.session_manager = session_manager
        self.allowed_user_ids = set(allowed_user_ids) if allowed_user_ids else None
        self.allowed_channel_ids = set(allowed_channel_ids) if allowed_channel_ids else None

        # Track user's current session
        self.user_sessions: dict[int, str] = {}  # user_id -> session_id

        # Track output streaming tasks
        self.stream_tasks: dict[int, asyncio.Task] = {}

        # Notification channel for hook events
        self.notification_channel_id: Optional[int] = None

        # Output summarizer and formatter
        self.summarizer = OutputSummarizer()
        self.formatter = create_formatter("discord")

    async def setup_hook(self) -> None:
        """Set up slash commands."""
        self.tree.add_command(cc_group)
        await self.tree.sync()
        logger.info("Discord bot commands synced")

        # Subscribe to hook events
        event_bus = get_event_bus()
        event_bus.subscribe(None, self._on_hook_event)  # Subscribe to all events
        logger.info("Discord bot subscribed to hook events")

    async def on_ready(self) -> None:
        """Handle bot ready event."""
        logger.info(f"Discord bot logged in as {self.user}")

    async def _on_hook_event(self, event: HookEvent) -> None:
        """Handle hook events from Claude Code.

        Sends notifications to users who are connected to the session.
        """
        logger.info(f"Discord bot received hook event: {event.event_type.value}")

        # Find users connected to this session
        users_to_notify = [
            user_id for user_id, session_id in self.user_sessions.items()
            if session_id == event.session_id
        ]

        if not users_to_notify:
            logger.debug(f"No users connected to session {event.session_id}")
            return

        # Format the message
        message = event.format_message()

        # Send to notification channel if set
        if self.notification_channel_id:
            try:
                channel = self.get_channel(self.notification_channel_id)
                if channel:
                    await channel.send(f"📢 **Hook Event**\n{message}")
            except Exception as e:
                logger.error(f"Failed to send to notification channel: {e}")

        # Send DM to each connected user
        for user_id in users_to_notify:
            try:
                user = await self.fetch_user(user_id)
                if user:
                    await user.send(f"📢 **알림**\n{message}")
                    logger.info(f"Sent hook notification to user {user_id}")
            except Exception as e:
                logger.error(f"Failed to send DM to user {user_id}: {e}")

    def set_notification_channel(self, channel_id: int) -> None:
        """Set the channel for hook event notifications.

        Args:
            channel_id: Discord channel ID
        """
        self.notification_channel_id = channel_id
        logger.info(f"Set notification channel to {channel_id}")

    def is_allowed(self, user_id: int) -> bool:
        """Check if user is allowed to use the bot."""
        if self.allowed_user_ids is None:
            return True
        return user_id in self.allowed_user_ids

    def _is_private_channel(self, channel: discord.abc.GuildChannel) -> bool:
        """Check if a guild channel is private (not visible to @everyone)."""
        if not hasattr(channel, "guild") or channel.guild is None:
            return False
        default_role = channel.guild.default_role
        perms = channel.permissions_for(default_role)
        return not perms.view_channel

    async def on_message(self, message: discord.Message) -> None:
        """Handle direct messages for natural conversation."""
        # Ignore bot messages
        if message.author.bot:
            return

        # Check various conditions for direct message handling
        is_dm = isinstance(message.channel, discord.DMChannel)
        is_mention = self.user in message.mentions if self.user else False

        # Check if channel is in allowed list or is a private channel
        is_allowed_channel = False
        is_private = False

        if isinstance(message.channel, discord.TextChannel):
            channel_id = message.channel.id
            # Explicitly allowed channel
            if self.allowed_channel_ids and channel_id in self.allowed_channel_ids:
                is_allowed_channel = True
            # Private channel (not visible to @everyone)
            elif self._is_private_channel(message.channel):
                is_private = True

        if not (is_dm or is_mention or is_allowed_channel or is_private):
            return

        # Check permission
        if not self.is_allowed(message.author.id):
            await message.reply("You are not authorized to use this bot.")
            return

        # Get user's current session
        session_id = self.user_sessions.get(message.author.id)
        if not session_id:
            await message.reply(
                "No session selected. Use `/cc list` to see sessions or `/cc new` to create one."
            )
            return

        # Remove mention from message if present
        content = message.content
        if is_mention and self.user:
            content = content.replace(f"<@{self.user.id}>", "").strip()

        if not content:
            return

        # Send to session
        try:
            logger.info(f"Sending input to session {session_id}: {content[:50]}...")

            # Send acknowledgement message first
            ack_msg = self.formatter.format_acknowledgement(content)
            status_msg = await message.channel.send(f"📨 {ack_msg.text}")

            await self.session_manager.send_input(session_id, content)
            await message.add_reaction("✅")

            # Cancel existing stream and start fresh for new input
            if message.author.id in self.stream_tasks:
                logger.info(f"Cancelling existing stream for user {message.author.id}")
                self.stream_tasks[message.author.id].cancel()
                self.stream_tasks.pop(message.author.id, None)

            # Start new output streaming with the status message
            logger.info(f"Starting new stream task for user {message.author.id}")
            task = asyncio.create_task(
                self._stream_output(
                    message.channel, session_id, message.author.id, status_msg
                )
            )
            self.stream_tasks[message.author.id] = task

        except SessionNotFoundError:
            self.user_sessions.pop(message.author.id, None)
            await message.reply("Session not found. It may have been deleted.")
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            await message.reply(f"Error: {e}")

    async def _stream_output(
        self,
        channel: discord.abc.Messageable,
        session_id: str,
        user_id: int,
        status_msg: Optional[discord.Message] = None,
    ) -> None:
        """Stream output from session to channel with summarized updates.

        Args:
            channel: Discord channel to send to
            session_id: Session ID
            user_id: User ID
            status_msg: Optional existing status message to update
        """
        logger.info(f"Starting stream for session {session_id}, user {user_id}")

        start_time = time.time()

        # Create status message if not provided
        if status_msg is None:
            status_msg = await channel.send("⏳ **처리 중...**")

        last_output = ""
        last_update = asyncio.get_event_loop().time()
        update_count = 0
        accumulated_output = ""

        try:
            # Stream with 2 second interval, 30 second idle timeout (for completion detection)
            # strip_ansi=True for Discord (no ANSI color support in code blocks)
            async for output in self.session_manager.stream_output(
                session_id, interval=2.0, idle_timeout=30, strip_ansi=True
            ):
                last_output = output
                accumulated_output = output  # Keep full output for analysis
                update_count += 1

                # Update message every 3 seconds to avoid rate limits
                now = asyncio.get_event_loop().time()
                if now - last_update > 3:
                    # Analyze output and format progress message
                    analysis = self.summarizer.analyze(accumulated_output)
                    progress_msg = self.formatter.format_progress(analysis, update_count)

                    # Build display - report only, no raw CLI output
                    display = f"⏳ {progress_msg.text}"

                    # Truncate if needed
                    if len(display) > 1900:
                        display = display[:1900] + "..."

                    try:
                        await status_msg.edit(content=display)
                    except discord.HTTPException as e:
                        logger.warning(f"Failed to edit message: {e}")
                    last_update = now

            # Final analysis
            elapsed = time.time() - start_time
            analysis = self.summarizer.analyze(accumulated_output)

            # Determine if error or completion
            if analysis.has_error:
                completion_msg = self.formatter.format_error(analysis)
                emoji = "❌"
            else:
                completion_msg = self.formatter.format_completion(
                    analysis, elapsed, raw_output=accumulated_output
                )
                emoji = "✅"

            # Build final message with readable output
            final_display = f"{emoji} {completion_msg.text}"

            # Add cleaned raw output preview
            raw_preview = self._get_readable_output(accumulated_output)
            if raw_preview:
                final_display += f"\n```\n{raw_preview}\n```"

            # Truncate if needed
            if len(final_display) > 1900:
                final_display = final_display[:1900] + "...\n```"

            try:
                await status_msg.edit(content=final_display)
            except discord.HTTPException:
                # If edit fails, send new message
                await channel.send(final_display)

        except asyncio.CancelledError:
            # Cancelled - update message
            try:
                await status_msg.edit(content="⚠️ **취소됨**")
            except discord.HTTPException:
                pass
            logger.debug(f"Stream cancelled for user {user_id}")
        except Exception as e:
            logger.error(f"Output streaming error: {e}")
            try:
                await status_msg.edit(content=f"❌ **오류:** {str(e)[:100]}")
            except discord.HTTPException:
                pass
        finally:
            self.stream_tasks.pop(user_id, None)
            logger.info(f"Stream task ended for user {user_id}")

    def _get_raw_preview(self, output: str, max_lines: int = 10) -> str:
        """Get last few lines of raw output for preview.

        Args:
            output: Raw output text
            max_lines: Maximum lines to show

        Returns:
            Preview string
        """
        if not output.strip():
            return ""

        lines = output.strip().split("\n")
        preview_lines = lines[-max_lines:] if len(lines) > max_lines else lines
        preview = "\n".join(preview_lines)

        # Truncate if too long
        if len(preview) > 800:
            preview = preview[-800:]

        return preview

    def _get_readable_output(self, output: str, max_lines: int = 50) -> str:
        """Get readable output with ANSI codes stripped.

        Args:
            output: Raw output text (may contain ANSI codes)
            max_lines: Maximum lines to show

        Returns:
            Cleaned, readable output string
        """
        if not output:
            return ""

        # Strip ANSI codes
        cleaned = strip_ansi(output)

        # Normalize line endings
        cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")

        # Split and filter lines
        lines = cleaned.strip().split("\n")

        # Filter out empty lines and common noise
        filtered_lines = []
        for line in lines:
            line = line.rstrip()
            # Skip empty lines at start/end
            if not line and not filtered_lines:
                continue
            # Skip common terminal noise
            if line.startswith(("$", "❯", "›")) and len(line) < 3:
                continue
            filtered_lines.append(line)

        # Remove trailing empty lines
        while filtered_lines and not filtered_lines[-1].strip():
            filtered_lines.pop()

        if not filtered_lines:
            return ""

        # Take last N lines
        if len(filtered_lines) > max_lines:
            filtered_lines = filtered_lines[-max_lines:]

        result = "\n".join(filtered_lines)

        # Truncate if too long (Discord limit ~2000, leave room for header)
        if len(result) > 1500:
            result = "..." + result[-1500:]

        return result

    def _format_progress(self, output: str, update_count: int) -> str:
        """Format progress message with last few lines of output."""
        lines = output.strip().split("\n")
        # Show last 10 lines max
        preview_lines = lines[-10:] if len(lines) > 10 else lines
        preview = "\n".join(preview_lines)

        # Truncate if too long
        if len(preview) > 1800:
            preview = preview[-1800:]

        return f"⏳ **진행 중...** (업데이트 #{update_count})\n```\n{preview}\n```"

    def _format_completion(self, output: str) -> str:
        """Format completion message with final output."""
        if not output.strip():
            return "✅ **완료**"

        lines = output.strip().split("\n")
        # Show last 15 lines for final result
        preview_lines = lines[-15:] if len(lines) > 15 else lines
        preview = "\n".join(preview_lines)

        # Truncate if too long
        if len(preview) > 1800:
            preview = preview[-1800:]

        return f"✅ **완료**\n```\n{preview}\n```"

    async def _send_output(self, channel: discord.abc.Messageable, text: str) -> None:
        """Send output to channel, splitting if necessary."""
        # Strip ANSI escape sequences and clean
        text = strip_ansi(text)
        # Normalize line endings (terminal uses \r\n)
        text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text:
            return

        # Split into chunks
        chunks = self._split_message(text)
        logger.info(f"Sending {len(chunks)} chunks to Discord")

        for chunk in chunks:
            await channel.send(f"```\n{chunk}\n```")
            await asyncio.sleep(0.5)  # Rate limiting

    def _split_message(self, text: str, max_length: int = MAX_MESSAGE_LENGTH) -> list[str]:
        """Split text into chunks that fit Discord's message limit."""
        if len(text) <= max_length:
            return [text]

        chunks = []
        lines = text.split("\n")
        current_chunk = ""

        for line in lines:
            if len(current_chunk) + len(line) + 1 > max_length:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = line
            else:
                if current_chunk:
                    current_chunk += "\n"
                current_chunk += line

        if current_chunk:
            chunks.append(current_chunk)

        return chunks


# Global bot instance (set by main.py)
bot: Optional[CCBot] = None


def set_bot(bot_instance: CCBot) -> None:
    """Set the global bot instance."""
    global bot
    bot = bot_instance


# Slash command group
cc_group = app_commands.Group(name="cc", description="Claude Code commands")


@cc_group.command(name="list", description="List all sessions")
async def cmd_list(interaction: discord.Interaction) -> None:
    """List all sessions."""
    if bot is None or not bot.is_allowed(interaction.user.id):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return

    sessions = bot.session_manager.list_sessions()

    if not sessions:
        await interaction.response.send_message("No sessions. Use `/cc new` to create one.")
        return

    # Get user's current session
    current_id = bot.user_sessions.get(interaction.user.id)

    lines = ["**Sessions:**"]
    for s in sessions:
        marker = "→ " if s.id == current_id else "  "
        lines.append(f"{marker}`{s.id}` **{s.name}** ({s.status.value})")

    await interaction.response.send_message("\n".join(lines))


@cc_group.command(name="new", description="Create a new session")
@app_commands.describe(
    name="Session name",
    working_dir="Working directory (default: ~)",
)
async def cmd_new(
    interaction: discord.Interaction,
    name: str,
    working_dir: Optional[str] = None,
) -> None:
    """Create a new session."""
    if bot is None or not bot.is_allowed(interaction.user.id):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return

    await interaction.response.defer()

    try:
        session = await bot.session_manager.create_session(name, working_dir)
        bot.user_sessions[interaction.user.id] = session.id

        await interaction.followup.send(
            f"✅ Created session **{name}** (`{session.id}`)\n"
            f"Working directory: `{session.working_dir}`\n"
            "You can now send messages to interact with Claude."
        )

    except SessionAlreadyExistsError:
        await interaction.followup.send(f"❌ Session '{name}' already exists.")
    except SessionLimitError:
        await interaction.followup.send("❌ Maximum session limit reached.")
    except Exception as e:
        logger.error(f"Failed to create session: {e}")
        await interaction.followup.send(f"❌ Error: {e}")


@cc_group.command(name="select", description="Select a session")
@app_commands.describe(name="Session name or ID")
async def cmd_select(interaction: discord.Interaction, name: str) -> None:
    """Select a session."""
    if bot is None or not bot.is_allowed(interaction.user.id):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return

    # Find session by name or ID
    session = bot.session_manager.get_session_by_name(name)
    if not session:
        try:
            session = bot.session_manager.get_session(name)
        except SessionNotFoundError:
            await interaction.response.send_message(f"❌ Session '{name}' not found.")
            return

    bot.user_sessions[interaction.user.id] = session.id
    await interaction.response.send_message(
        f"✅ Selected session **{session.name}** (`{session.id}`)"
    )


@cc_group.command(name="send", description="Send a message to current session")
@app_commands.describe(message="Message to send")
async def cmd_send(interaction: discord.Interaction, message: str) -> None:
    """Send a message to the current session."""
    if bot is None or not bot.is_allowed(interaction.user.id):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return

    session_id = bot.user_sessions.get(interaction.user.id)
    if not session_id:
        await interaction.response.send_message(
            "No session selected. Use `/cc select` first."
        )
        return

    await interaction.response.defer()

    try:
        # Send acknowledgement message
        ack_msg = bot.formatter.format_acknowledgement(message)
        status_msg = await interaction.followup.send(f"📨 {ack_msg.text}")

        await bot.session_manager.send_input(session_id, message)

        # Cancel existing stream and start fresh
        if interaction.user.id in bot.stream_tasks:
            bot.stream_tasks[interaction.user.id].cancel()
            bot.stream_tasks.pop(interaction.user.id, None)

        # Start new output streaming with the status message
        if interaction.channel:
            task = asyncio.create_task(
                bot._stream_output(
                    interaction.channel, session_id, interaction.user.id, status_msg
                )
            )
            bot.stream_tasks[interaction.user.id] = task

    except SessionNotFoundError:
        bot.user_sessions.pop(interaction.user.id, None)
        await interaction.followup.send("❌ Session not found.")
    except Exception as e:
        logger.error(f"Failed to send message: {e}")
        await interaction.followup.send(f"❌ Error: {e}")


@cc_group.command(name="cancel", description="Cancel running command (Ctrl+C)")
async def cmd_cancel(interaction: discord.Interaction) -> None:
    """Cancel the current command."""
    if bot is None or not bot.is_allowed(interaction.user.id):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return

    session_id = bot.user_sessions.get(interaction.user.id)
    if not session_id:
        await interaction.response.send_message("No session selected.")
        return

    try:
        await bot.session_manager.cancel_command(session_id)
        await interaction.response.send_message("✅ Sent Ctrl+C")
    except SessionNotFoundError:
        bot.user_sessions.pop(interaction.user.id, None)
        await interaction.response.send_message("❌ Session not found.")
    except Exception as e:
        await interaction.response.send_message(f"❌ Error: {e}")


@cc_group.command(name="status", description="Show current session status")
async def cmd_status(interaction: discord.Interaction) -> None:
    """Show current session status."""
    if bot is None or not bot.is_allowed(interaction.user.id):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return

    session_id = bot.user_sessions.get(interaction.user.id)
    if not session_id:
        await interaction.response.send_message("No session selected.")
        return

    try:
        session = bot.session_manager.get_session(session_id)
        alive = await bot.session_manager.check_session_alive(session_id)

        status_emoji = {
            "active": "🟢",
            "idle": "🟡",
            "waiting_input": "🟠",
            "starting": "🔵",
            "stopped": "🔴",
            "error": "❌",
        }

        emoji = status_emoji.get(session.status.value, "⚪")

        await interaction.response.send_message(
            f"**Session:** {session.name}\n"
            f"**ID:** `{session.id}`\n"
            f"**Status:** {emoji} {session.status.value}\n"
            f"**Working Dir:** `{session.working_dir}`\n"
            f"**Alive:** {'Yes' if alive else 'No'}\n"
            f"**Last Activity:** {session.last_activity.strftime('%H:%M:%S')}"
        )
    except SessionNotFoundError:
        bot.user_sessions.pop(interaction.user.id, None)
        await interaction.response.send_message("❌ Session not found.")


@cc_group.command(name="discover", description="Discover WezTerm panes")
async def cmd_discover(interaction: discord.Interaction) -> None:
    """Discover all WezTerm panes in the workspace."""
    if bot is None or not bot.is_allowed(interaction.user.id):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return

    await interaction.response.defer()

    try:
        panes = await bot.session_manager.discover_wezterm_panes()

        if not panes:
            await interaction.followup.send("No WezTerm panes found in the workspace.")
            return

        # Get managed pane IDs
        managed_pane_ids = {s.wezterm_pane_id for s in bot.session_manager.list_sessions()}

        lines = ["**Discovered WezTerm panes:**\n"]
        managed_count = 0
        external_count = 0

        for p in panes:
            pane_id = p.get("pane_id")
            if pane_id in managed_pane_ids:
                managed_count += 1
                lines.append(f"✅ Pane `{pane_id}` - managed")
            else:
                external_count += 1
                lines.append(f"🔵 Pane `{pane_id}` - external")

        lines.append(f"\n**Summary:** {managed_count} managed, {external_count} external")
        if external_count > 0:
            lines.append("Use `/cc import <pane_id>` to import external panes.")

        await interaction.followup.send("\n".join(lines))

    except Exception as e:
        logger.error(f"Failed to discover panes: {e}")
        await interaction.followup.send(f"❌ Error: {e}")


@cc_group.command(name="import", description="Import WezTerm pane")
@app_commands.describe(
    pane_id="WezTerm pane ID to import (use 'all' to import all)",
    name="Custom name for the session",
)
async def cmd_import(
    interaction: discord.Interaction,
    pane_id: str,
    name: Optional[str] = None,
) -> None:
    """Import an external WezTerm pane."""
    if bot is None or not bot.is_allowed(interaction.user.id):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return

    await interaction.response.defer()

    try:
        if pane_id.lower() == "all":
            # Import all external panes
            imported = await bot.session_manager.import_all_sessions()

            if not imported:
                await interaction.followup.send("No external panes to import.")
                return

            lines = ["**Imported sessions:**"]
            for s in imported:
                lines.append(f"✅ `{s.id}` **{s.name}**")

            await interaction.followup.send("\n".join(lines))
        else:
            # Import specific pane
            session = await bot.session_manager.import_session(int(pane_id), name)
            bot.user_sessions[interaction.user.id] = session.id

            await interaction.followup.send(
                f"✅ Imported session **{session.name}** (`{session.id}`)\n"
                f"Working directory: `{session.working_dir}`\n"
                "Session is now selected."
            )

    except SessionAlreadyExistsError:
        await interaction.followup.send(f"❌ Pane '{pane_id}' is already managed.")
    except ValueError:
        await interaction.followup.send(f"❌ Invalid pane ID: {pane_id}")
    except Exception as e:
        logger.error(f"Failed to import session: {e}")
        await interaction.followup.send(f"❌ Error: {e}")


@cc_group.command(name="delete", description="Delete a session")
@app_commands.describe(name="Session name or ID (default: current)")
async def cmd_delete(
    interaction: discord.Interaction,
    name: Optional[str] = None,
) -> None:
    """Delete a session."""
    if bot is None or not bot.is_allowed(interaction.user.id):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return

    # Get session to delete
    if name:
        session = bot.session_manager.get_session_by_name(name)
        if not session:
            try:
                session = bot.session_manager.get_session(name)
            except SessionNotFoundError:
                await interaction.response.send_message(f"❌ Session '{name}' not found.")
                return
        session_id = session.id
    else:
        session_id = bot.user_sessions.get(interaction.user.id)
        if not session_id:
            await interaction.response.send_message("No session selected.")
            return

    await interaction.response.defer()

    try:
        session = bot.session_manager.get_session(session_id)
        session_name = session.name

        await bot.session_manager.destroy_session(session_id)

        # Clear user's current session if deleted
        if bot.user_sessions.get(interaction.user.id) == session_id:
            bot.user_sessions.pop(interaction.user.id, None)

        await interaction.followup.send(f"✅ Deleted session **{session_name}**")

    except SessionNotFoundError:
        await interaction.followup.send("❌ Session not found.")
    except Exception as e:
        logger.error(f"Failed to delete session: {e}")
        await interaction.followup.send(f"❌ Error: {e}")


async def run_bot(
    token: str,
    session_manager: SessionManager,
    allowed_user_ids: Optional[list[int]] = None,
    allowed_channel_ids: Optional[list[int]] = None,
) -> None:
    """Run the Discord bot.

    Args:
        token: Discord bot token
        session_manager: SessionManager instance
        allowed_user_ids: List of allowed user IDs
        allowed_channel_ids: List of channel IDs where direct messages work
    """
    global bot
    bot = CCBot(
        session_manager=session_manager,
        allowed_user_ids=allowed_user_ids,
        allowed_channel_ids=allowed_channel_ids,
    )
    set_bot(bot)

    try:
        await bot.start(token)
    finally:
        await bot.close()
