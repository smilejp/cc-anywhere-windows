"""Slack Bot for CC-Anywhere."""

import asyncio
import logging
import re
import time
from typing import Optional

from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp

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

# Maximum message length for Slack
MAX_MESSAGE_LENGTH = 2900


class SlackBot:
    """CC-Anywhere Slack Bot."""

    def __init__(
        self,
        bot_token: str,
        app_token: str,
        session_manager: SessionManager,
        allowed_user_ids: Optional[list[str]] = None,
    ):
        """Initialize the bot.

        Args:
            bot_token: Slack bot token (xoxb-...)
            app_token: Slack app token (xapp-...)
            session_manager: SessionManager instance
            allowed_user_ids: List of allowed Slack user IDs
        """
        self.session_manager = session_manager
        self.allowed_user_ids = set(allowed_user_ids) if allowed_user_ids else None

        # Track user's current session
        self.user_sessions: dict[str, str] = {}

        # Track user's DM channel for notifications
        self.user_dm_channels: dict[str, str] = {}

        # Output summarizer and formatter
        self.summarizer = OutputSummarizer()
        self.formatter = create_formatter("slack")

        # Create app
        self.app = AsyncApp(token=bot_token)
        self.handler = AsyncSocketModeHandler(self.app, app_token)
        self._slack_client = None  # Will be set when bot starts

        self._setup_handlers()

        # Subscribe to hook events
        event_bus = get_event_bus()
        event_bus.subscribe(None, self._on_hook_event)
        logger.info("Slack bot subscribed to hook events")

    async def _on_hook_event(self, event: HookEvent) -> None:
        """Handle hook events from Claude Code."""
        logger.info(f"Slack bot received hook event: {event.event_type.value}")

        # Find users connected to this session
        users_to_notify = [
            user_id for user_id, session_id in self.user_sessions.items()
            if session_id == event.session_id
        ]

        if not users_to_notify or not self._slack_client:
            return

        message = event.format_message()

        # Send to each connected user's DM
        for user_id in users_to_notify:
            try:
                # Get or open DM channel
                if user_id not in self.user_dm_channels:
                    result = await self._slack_client.conversations_open(users=[user_id])
                    if result.get("ok"):
                        self.user_dm_channels[user_id] = result["channel"]["id"]

                channel = self.user_dm_channels.get(user_id)
                if channel:
                    await self._slack_client.chat_postMessage(
                        channel=channel,
                        text=f":mega: *알림*\n{message}",
                    )
                    logger.info(f"Sent hook notification to Slack user {user_id}")
            except Exception as e:
                logger.error(f"Failed to send Slack notification to {user_id}: {e}")

    def _setup_handlers(self) -> None:
        """Set up event handlers."""
        # Slash commands
        self.app.command("/cc")(self._handle_command)

        # App mentions
        self.app.event("app_mention")(self._handle_mention)

        # Direct messages
        self.app.event("message")(self._handle_message)

    def _is_allowed(self, user_id: str) -> bool:
        """Check if user is allowed."""
        if self.allowed_user_ids is None:
            return True
        return user_id in self.allowed_user_ids

    async def _handle_command(self, ack, command, client) -> None:
        """Handle /cc slash command."""
        await ack()

        user_id = command["user_id"]
        if not self._is_allowed(user_id):
            await client.chat_postMessage(
                channel=command["channel_id"],
                text="❌ Not authorized.",
            )
            return

        text = command.get("text", "").strip()
        parts = text.split(maxsplit=1)
        subcommand = parts[0] if parts else "help"
        args = parts[1] if len(parts) > 1 else ""

        channel = command["channel_id"]

        if subcommand == "help":
            await self._cmd_help(client, channel)
        elif subcommand == "list":
            await self._cmd_list(client, channel, user_id)
        elif subcommand == "new":
            await self._cmd_new(client, channel, user_id, args)
        elif subcommand == "select":
            await self._cmd_select(client, channel, user_id, args)
        elif subcommand == "send":
            await self._cmd_send(client, channel, user_id, args)
        elif subcommand == "cancel":
            await self._cmd_cancel(client, channel, user_id)
        elif subcommand == "status":
            await self._cmd_status(client, channel, user_id)
        elif subcommand == "delete":
            await self._cmd_delete(client, channel, user_id, args)
        else:
            await client.chat_postMessage(
                channel=channel,
                text=f"Unknown command: {subcommand}. Use `/cc help` for usage.",
            )

    async def _cmd_help(self, client, channel: str) -> None:
        """Send help message."""
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "CC-Anywhere Commands"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "`/cc list` - List sessions\n"
                        "`/cc new <name> [dir]` - Create session\n"
                        "`/cc select <name>` - Select session\n"
                        "`/cc send <message>` - Send to Claude\n"
                        "`/cc cancel` - Cancel command (Ctrl+C)\n"
                        "`/cc status` - Session status\n"
                        "`/cc delete [name]` - Delete session"
                    ),
                },
            },
        ]

        await client.chat_postMessage(channel=channel, blocks=blocks)

    async def _cmd_list(self, client, channel: str, user_id: str) -> None:
        """List sessions."""
        sessions = self.session_manager.list_sessions()

        if not sessions:
            await client.chat_postMessage(
                channel=channel,
                text="No sessions. Use `/cc new <name>` to create one.",
            )
            return

        current_id = self.user_sessions.get(user_id)

        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": "Sessions"}},
        ]

        for s in sessions:
            status_emoji = {
                "active": ":large_green_circle:",
                "idle": ":large_yellow_circle:",
                "waiting_input": ":large_orange_circle:",
                "starting": ":large_blue_circle:",
                "stopped": ":red_circle:",
            }.get(s.status.value, ":white_circle:")

            marker = ":arrow_right: " if s.id == current_id else ""

            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"{marker}{status_emoji} *{s.name}* (`{s.id}`)",
                },
                "accessory": {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Select"},
                    "action_id": f"select_session_{s.id}",
                },
            })

        await client.chat_postMessage(channel=channel, blocks=blocks)

    async def _cmd_new(self, client, channel: str, user_id: str, args: str) -> None:
        """Create new session."""
        parts = args.split(maxsplit=1)
        if not parts:
            await client.chat_postMessage(
                channel=channel,
                text="Usage: `/cc new <name> [working_dir]`",
            )
            return

        name = parts[0]
        working_dir = parts[1] if len(parts) > 1 else None

        try:
            session = await self.session_manager.create_session(name, working_dir)
            self.user_sessions[user_id] = session.id

            await client.chat_postMessage(
                channel=channel,
                text=(
                    f":white_check_mark: Created session *{name}*\n"
                    f"Working directory: `{session.working_dir}`"
                ),
            )

        except SessionAlreadyExistsError:
            await client.chat_postMessage(
                channel=channel,
                text=f":x: Session '{name}' already exists.",
            )
        except SessionLimitError:
            await client.chat_postMessage(
                channel=channel,
                text=":x: Maximum session limit reached.",
            )
        except Exception as e:
            logger.error(f"Failed to create session: {e}")
            await client.chat_postMessage(channel=channel, text=f":x: Error: {e}")

    async def _cmd_select(self, client, channel: str, user_id: str, name: str) -> None:
        """Select session."""
        if not name:
            await self._cmd_list(client, channel, user_id)
            return

        session = self.session_manager.get_session_by_name(name)
        if not session:
            try:
                session = self.session_manager.get_session(name)
            except SessionNotFoundError:
                await client.chat_postMessage(
                    channel=channel,
                    text=f":x: Session '{name}' not found.",
                )
                return

        self.user_sessions[user_id] = session.id
        await client.chat_postMessage(
            channel=channel,
            text=f":white_check_mark: Selected session *{session.name}*",
        )

    async def _cmd_send(self, client, channel: str, user_id: str, message: str) -> None:
        """Send message to session."""
        if not message:
            await client.chat_postMessage(
                channel=channel,
                text="Usage: `/cc send <message>`",
            )
            return

        session_id = self.user_sessions.get(user_id)
        if not session_id:
            await client.chat_postMessage(
                channel=channel,
                text="No session selected. Use `/cc select` first.",
            )
            return

        try:
            start_time = time.time()

            # Send acknowledgement message first
            ack_msg = self.formatter.format_acknowledgement(message)
            result = await client.chat_postMessage(
                channel=channel,
                text=f":incoming_envelope: {ack_msg.text}",
            )
            status_ts = result.get("ts")

            await self.session_manager.send_input(session_id, message)

            # Wait for response and read more history
            await asyncio.sleep(2)
            output = await self.session_manager.read_output(session_id, lines=200)

            elapsed = time.time() - start_time
            raw_content = output.content.strip()

            if raw_content:
                # Analyze output with pattern matching
                analysis = self.summarizer.analyze(raw_content)

                # Determine message type and format
                if analysis.has_error:
                    formatted = self.formatter.format_error(analysis)
                    emoji = ":x:"
                else:
                    formatted = self.formatter.format_completion(
                        analysis, elapsed, raw_output=raw_content
                    )
                    emoji = ":white_check_mark:"

                # Build final message with readable output
                final_text = f"{emoji} {formatted.text}"

                # Add cleaned raw output preview
                raw_preview = self._get_readable_output(raw_content)
                if raw_preview:
                    final_text += f"\n```\n{raw_preview}\n```"

                # Truncate if needed (Slack limit ~3000)
                if len(final_text) > 2800:
                    final_text = final_text[:2800] + "...\n```"

                # Update the status message with completion
                try:
                    await client.chat_update(
                        channel=channel,
                        ts=status_ts,
                        text=final_text,
                    )
                except Exception:
                    # If update fails, send new message
                    await client.chat_postMessage(channel=channel, text=final_text)
            else:
                # No output - update with simple completion
                try:
                    await client.chat_update(
                        channel=channel,
                        ts=status_ts,
                        text=":white_check_mark: 완료 (출력 없음)",
                    )
                except Exception:
                    pass

        except SessionNotFoundError:
            self.user_sessions.pop(user_id, None)
            await client.chat_postMessage(channel=channel, text=":x: Session not found.")
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            await client.chat_postMessage(channel=channel, text=f":x: Error: {e}")

    async def _cmd_cancel(self, client, channel: str, user_id: str) -> None:
        """Cancel command."""
        session_id = self.user_sessions.get(user_id)
        if not session_id:
            await client.chat_postMessage(
                channel=channel,
                text="No session selected.",
            )
            return

        try:
            await self.session_manager.cancel_command(session_id)
            await client.chat_postMessage(
                channel=channel,
                text=":white_check_mark: Sent Ctrl+C",
            )
        except SessionNotFoundError:
            self.user_sessions.pop(user_id, None)
            await client.chat_postMessage(channel=channel, text=":x: Session not found.")

    async def _cmd_status(self, client, channel: str, user_id: str) -> None:
        """Show session status."""
        session_id = self.user_sessions.get(user_id)
        if not session_id:
            await client.chat_postMessage(channel=channel, text="No session selected.")
            return

        try:
            session = self.session_manager.get_session(session_id)
            alive = await self.session_manager.check_session_alive(session_id)

            status_emoji = {
                "active": ":large_green_circle:",
                "idle": ":large_yellow_circle:",
                "waiting_input": ":large_orange_circle:",
            }.get(session.status.value, ":white_circle:")

            await client.chat_postMessage(
                channel=channel,
                text=(
                    f"*Session:* {session.name}\n"
                    f"*Status:* {status_emoji} {session.status.value}\n"
                    f"*Working Dir:* `{session.working_dir}`\n"
                    f"*Alive:* {'Yes' if alive else 'No'}"
                ),
            )
        except SessionNotFoundError:
            self.user_sessions.pop(user_id, None)
            await client.chat_postMessage(channel=channel, text=":x: Session not found.")

    async def _cmd_delete(self, client, channel: str, user_id: str, name: str) -> None:
        """Delete session."""
        if name:
            session = self.session_manager.get_session_by_name(name)
            if not session:
                try:
                    session = self.session_manager.get_session(name)
                except SessionNotFoundError:
                    await client.chat_postMessage(
                        channel=channel,
                        text=f":x: Session '{name}' not found.",
                    )
                    return
            session_id = session.id
        else:
            session_id = self.user_sessions.get(user_id)
            if not session_id:
                await client.chat_postMessage(channel=channel, text="No session selected.")
                return

        try:
            session = self.session_manager.get_session(session_id)
            session_name = session.name

            await self.session_manager.destroy_session(session_id)

            if self.user_sessions.get(user_id) == session_id:
                self.user_sessions.pop(user_id, None)

            await client.chat_postMessage(
                channel=channel,
                text=f":white_check_mark: Deleted session *{session_name}*",
            )
        except SessionNotFoundError:
            await client.chat_postMessage(channel=channel, text=":x: Session not found.")

    async def _handle_mention(self, event, client) -> None:
        """Handle app mentions."""
        user_id = event.get("user")
        if not user_id or not self._is_allowed(user_id):
            return

        text = event.get("text", "")
        # Remove mention
        text = re.sub(r"<@[A-Z0-9]+>", "", text).strip()

        if not text:
            return

        channel = event.get("channel")
        session_id = self.user_sessions.get(user_id)

        if not session_id:
            await client.chat_postMessage(
                channel=channel,
                text="No session selected. Use `/cc select` first.",
            )
            return

        try:
            start_time = time.time()

            # Send acknowledgement message first
            ack_msg = self.formatter.format_acknowledgement(text)
            result = await client.chat_postMessage(
                channel=channel,
                text=f":incoming_envelope: {ack_msg.text}",
            )
            status_ts = result.get("ts")

            await self.session_manager.send_input(session_id, text)

            # Wait for response and read more history
            await asyncio.sleep(2)
            output = await self.session_manager.read_output(session_id, lines=200)

            elapsed = time.time() - start_time
            raw_content = output.content.strip()

            if raw_content:
                # Analyze output with pattern matching
                analysis = self.summarizer.analyze(raw_content)

                # Determine message type and format
                if analysis.has_error:
                    formatted = self.formatter.format_error(analysis)
                    emoji = ":x:"
                else:
                    formatted = self.formatter.format_completion(
                        analysis, elapsed, raw_output=raw_content
                    )
                    emoji = ":white_check_mark:"

                # Build final message with readable output
                final_text = f"{emoji} {formatted.text}"

                # Add cleaned raw output preview
                raw_preview = self._get_readable_output(raw_content)
                if raw_preview:
                    final_text += f"\n```\n{raw_preview}\n```"

                # Truncate if needed
                if len(final_text) > 2800:
                    final_text = final_text[:2800] + "...\n```"

                # Update the status message
                try:
                    await client.chat_update(
                        channel=channel,
                        ts=status_ts,
                        text=final_text,
                    )
                except Exception:
                    await client.chat_postMessage(channel=channel, text=final_text)
            else:
                try:
                    await client.chat_update(
                        channel=channel,
                        ts=status_ts,
                        text=":white_check_mark: 완료 (출력 없음)",
                    )
                except Exception:
                    pass

        except SessionNotFoundError:
            self.user_sessions.pop(user_id, None)
            await client.chat_postMessage(channel=channel, text=":x: Session not found.")
        except Exception as e:
            logger.error(f"Failed to handle mention: {e}")

    async def _handle_message(self, event, client) -> None:
        """Handle direct messages."""
        # Only handle DMs
        if event.get("channel_type") != "im":
            return

        user_id = event.get("user")
        if not user_id or not self._is_allowed(user_id):
            return

        # Ignore bot messages
        if event.get("bot_id"):
            return

        text = event.get("text", "").strip()
        if not text:
            return

        channel = event.get("channel")
        session_id = self.user_sessions.get(user_id)

        if not session_id:
            await client.chat_postMessage(
                channel=channel,
                text="No session selected. Use `/cc select` first.",
            )
            return

        try:
            start_time = time.time()

            # Send acknowledgement message first
            ack_msg = self.formatter.format_acknowledgement(text)
            result = await client.chat_postMessage(
                channel=channel,
                text=f":incoming_envelope: {ack_msg.text}",
            )
            status_ts = result.get("ts")

            await self.session_manager.send_input(session_id, text)

            # Wait for response and read more history
            await asyncio.sleep(2)
            output = await self.session_manager.read_output(session_id, lines=200)

            elapsed = time.time() - start_time
            raw_content = output.content.strip()

            if raw_content:
                # Analyze output with pattern matching
                analysis = self.summarizer.analyze(raw_content)

                # Determine message type and format
                if analysis.has_error:
                    formatted = self.formatter.format_error(analysis)
                    emoji = ":x:"
                else:
                    formatted = self.formatter.format_completion(
                        analysis, elapsed, raw_output=raw_content
                    )
                    emoji = ":white_check_mark:"

                # Build final message with readable output
                final_text = f"{emoji} {formatted.text}"

                # Add cleaned raw output preview
                raw_preview = self._get_readable_output(raw_content)
                if raw_preview:
                    final_text += f"\n```\n{raw_preview}\n```"

                # Truncate if needed
                if len(final_text) > 2800:
                    final_text = final_text[:2800] + "...\n```"

                # Update the status message
                try:
                    await client.chat_update(
                        channel=channel,
                        ts=status_ts,
                        text=final_text,
                    )
                except Exception:
                    await client.chat_postMessage(channel=channel, text=final_text)
            else:
                try:
                    await client.chat_update(
                        channel=channel,
                        ts=status_ts,
                        text=":white_check_mark: 완료 (출력 없음)",
                    )
                except Exception:
                    pass

        except SessionNotFoundError:
            self.user_sessions.pop(user_id, None)
            await client.chat_postMessage(channel=channel, text=":x: Session not found.")
        except Exception as e:
            logger.error(f"Failed to handle message: {e}")

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

        # Truncate if too long (Slack has ~3000 char limit for messages)
        if len(preview) > 1200:
            preview = preview[-1200:]

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
        ansi_pattern = re.compile(
            r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[PX^_][^\x1b]*\x1b\\"
        )
        cleaned = ansi_pattern.sub("", output)

        # Normalize line endings
        cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")

        # Split and filter lines
        lines = cleaned.strip().split("\n")

        # Filter out empty lines and common noise
        filtered_lines = []
        for line in lines:
            line = line.rstrip()
            if not line and not filtered_lines:
                continue
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

        # Truncate if too long (Slack limit ~3000, leave room for header)
        if len(result) > 2200:
            result = "..." + result[-2200:]

        return result

    async def _send_output(self, client, channel: str, text: str) -> None:
        """Send output, splitting if necessary."""
        chunks = self._split_message(text)

        for chunk in chunks[:5]:
            await client.chat_postMessage(
                channel=channel,
                text=f"```{chunk}```",
            )
            await asyncio.sleep(0.5)

    def _split_message(self, text: str, max_length: int = MAX_MESSAGE_LENGTH) -> list[str]:
        """Split text into chunks."""
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

    async def run(self) -> None:
        """Run the bot."""
        logger.info("Starting Slack bot...")
        # Store client reference for hook notifications
        self._slack_client = self.app.client
        await self.handler.start_async()


async def run_bot(
    bot_token: str,
    app_token: str,
    session_manager: SessionManager,
    allowed_user_ids: Optional[list[str]] = None,
) -> None:
    """Run the Slack bot."""
    bot = SlackBot(bot_token, app_token, session_manager, allowed_user_ids)
    await bot.run()
