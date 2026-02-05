"""Telegram Bot for CC-Anywhere."""

import asyncio
import logging
import time
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

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

# Maximum message length for Telegram
MAX_MESSAGE_LENGTH = 4000


class TelegramBot:
    """CC-Anywhere Telegram Bot."""

    def __init__(
        self,
        token: str,
        session_manager: SessionManager,
        allowed_user_ids: Optional[list[int]] = None,
    ):
        """Initialize the bot.

        Args:
            token: Telegram bot token
            session_manager: SessionManager instance
            allowed_user_ids: List of allowed Telegram user IDs
        """
        self.token = token
        self.session_manager = session_manager
        self.allowed_user_ids = set(allowed_user_ids) if allowed_user_ids else None

        # Track user's current session
        self.user_sessions: dict[int, str] = {}

        # Output summarizer and formatter
        self.summarizer = OutputSummarizer()
        self.formatter = create_formatter("telegram")

        # Build application
        self.app = Application.builder().token(token).build()
        self._setup_handlers()

        # Subscribe to hook events
        event_bus = get_event_bus()
        event_bus.subscribe(None, self._on_hook_event)
        logger.info("Telegram bot subscribed to hook events")

    async def _on_hook_event(self, event: HookEvent) -> None:
        """Handle hook events from Claude Code."""
        logger.info(f"Telegram bot received hook event: {event.event_type.value}")

        # Find users connected to this session
        users_to_notify = [
            user_id for user_id, session_id in self.user_sessions.items()
            if session_id == event.session_id
        ]

        if not users_to_notify:
            return

        message = event.format_message()

        # Send to each connected user
        for user_id in users_to_notify:
            try:
                await self.app.bot.send_message(
                    chat_id=user_id,
                    text=f"📢 *알림*\n{message}",
                    parse_mode="Markdown",
                )
                logger.info(f"Sent hook notification to Telegram user {user_id}")
            except Exception as e:
                logger.error(f"Failed to send Telegram notification to {user_id}: {e}")

    def _setup_handlers(self) -> None:
        """Set up message handlers."""
        # Commands
        self.app.add_handler(CommandHandler("start", self._cmd_start))
        self.app.add_handler(CommandHandler("help", self._cmd_help))
        self.app.add_handler(CommandHandler("list", self._cmd_list))
        self.app.add_handler(CommandHandler("new", self._cmd_new))
        self.app.add_handler(CommandHandler("select", self._cmd_select))
        self.app.add_handler(CommandHandler("cancel", self._cmd_cancel))
        self.app.add_handler(CommandHandler("status", self._cmd_status))
        self.app.add_handler(CommandHandler("delete", self._cmd_delete))

        # Callback queries (inline buttons)
        self.app.add_handler(CallbackQueryHandler(self._handle_callback))

        # Text messages (natural conversation)
        self.app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
        )

    def _is_allowed(self, user_id: int) -> bool:
        """Check if user is allowed."""
        if self.allowed_user_ids is None:
            return True
        return user_id in self.allowed_user_ids

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command."""
        if not update.effective_user or not self._is_allowed(update.effective_user.id):
            return

        await update.message.reply_text(
            "👋 *Welcome to CC-Anywhere!*\n\n"
            "I help you interact with Claude Code remotely.\n\n"
            "*Commands:*\n"
            "/list - List sessions\n"
            "/new <name> - Create session\n"
            "/select - Select session\n"
            "/cancel - Cancel command (Ctrl+C)\n"
            "/status - Session status\n"
            "/delete - Delete session\n\n"
            "After selecting a session, just send messages to chat with Claude!",
            parse_mode="Markdown",
        )

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /help command."""
        await self._cmd_start(update, context)

    async def _cmd_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /list command."""
        if not update.effective_user or not self._is_allowed(update.effective_user.id):
            return

        sessions = self.session_manager.list_sessions()

        if not sessions:
            await update.message.reply_text("No sessions. Use /new <name> to create one.")
            return

        current_id = self.user_sessions.get(update.effective_user.id)

        lines = ["*Sessions:*"]
        for s in sessions:
            marker = "→ " if s.id == current_id else "  "
            status_emoji = {
                "active": "🟢",
                "idle": "🟡",
                "waiting_input": "🟠",
                "starting": "🔵",
                "stopped": "🔴",
                "error": "❌",
            }.get(s.status.value, "⚪")

            lines.append(f"{marker}{status_emoji} `{s.name}`")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def _cmd_new(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /new command."""
        if not update.effective_user or not self._is_allowed(update.effective_user.id):
            return

        if not context.args:
            await update.message.reply_text("Usage: /new <name> [working_dir]")
            return

        name = context.args[0]
        working_dir = context.args[1] if len(context.args) > 1 else None

        try:
            session = await self.session_manager.create_session(name, working_dir)
            self.user_sessions[update.effective_user.id] = session.id

            await update.message.reply_text(
                f"✅ Created session *{name}*\n"
                f"Working directory: `{session.working_dir}`\n\n"
                "You can now send messages to chat with Claude!",
                parse_mode="Markdown",
            )

        except SessionAlreadyExistsError:
            await update.message.reply_text(f"❌ Session '{name}' already exists.")
        except SessionLimitError:
            await update.message.reply_text("❌ Maximum session limit reached.")
        except Exception as e:
            logger.error(f"Failed to create session: {e}")
            await update.message.reply_text(f"❌ Error: {e}")

    async def _cmd_select(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /select command."""
        if not update.effective_user or not self._is_allowed(update.effective_user.id):
            return

        sessions = self.session_manager.list_sessions()

        if not sessions:
            await update.message.reply_text("No sessions. Use /new <name> to create one.")
            return

        # Create inline keyboard
        keyboard = []
        for s in sessions:
            status_emoji = {
                "active": "🟢",
                "idle": "🟡",
                "waiting_input": "🟠",
            }.get(s.status.value, "⚪")

            keyboard.append([
                InlineKeyboardButton(
                    f"{status_emoji} {s.name}",
                    callback_data=f"select:{s.id}",
                )
            ])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Select a session:", reply_markup=reply_markup)

    async def _cmd_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /cancel command."""
        if not update.effective_user or not self._is_allowed(update.effective_user.id):
            return

        session_id = self.user_sessions.get(update.effective_user.id)
        if not session_id:
            await update.message.reply_text("No session selected. Use /select first.")
            return

        try:
            await self.session_manager.cancel_command(session_id)
            await update.message.reply_text("✅ Sent Ctrl+C")
        except SessionNotFoundError:
            self.user_sessions.pop(update.effective_user.id, None)
            await update.message.reply_text("❌ Session not found.")
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /status command."""
        if not update.effective_user or not self._is_allowed(update.effective_user.id):
            return

        session_id = self.user_sessions.get(update.effective_user.id)
        if not session_id:
            await update.message.reply_text("No session selected.")
            return

        try:
            session = self.session_manager.get_session(session_id)
            alive = await self.session_manager.check_session_alive(session_id)

            status_emoji = {
                "active": "🟢",
                "idle": "🟡",
                "waiting_input": "🟠",
                "starting": "🔵",
                "stopped": "🔴",
                "error": "❌",
            }.get(session.status.value, "⚪")

            await update.message.reply_text(
                f"*Session:* {session.name}\n"
                f"*Status:* {status_emoji} {session.status.value}\n"
                f"*Working Dir:* `{session.working_dir}`\n"
                f"*Alive:* {'Yes' if alive else 'No'}\n"
                f"*Last Activity:* {session.last_activity.strftime('%H:%M:%S')}",
                parse_mode="Markdown",
            )
        except SessionNotFoundError:
            self.user_sessions.pop(update.effective_user.id, None)
            await update.message.reply_text("❌ Session not found.")

    async def _cmd_delete(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /delete command."""
        if not update.effective_user or not self._is_allowed(update.effective_user.id):
            return

        session_id = self.user_sessions.get(update.effective_user.id)
        if not session_id:
            await update.message.reply_text("No session selected.")
            return

        try:
            session = self.session_manager.get_session(session_id)

            # Confirm deletion with inline button
            keyboard = [[
                InlineKeyboardButton("✅ Yes, delete", callback_data=f"delete:{session_id}"),
                InlineKeyboardButton("❌ Cancel", callback_data="delete:cancel"),
            ]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text(
                f"Delete session *{session.name}*?",
                reply_markup=reply_markup,
                parse_mode="Markdown",
            )
        except SessionNotFoundError:
            self.user_sessions.pop(update.effective_user.id, None)
            await update.message.reply_text("❌ Session not found.")

    async def _handle_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle callback queries from inline buttons."""
        query = update.callback_query
        if not query or not query.from_user:
            return

        if not self._is_allowed(query.from_user.id):
            await query.answer("Not authorized.")
            return

        await query.answer()

        data = query.data
        if data.startswith("select:"):
            session_id = data.split(":")[1]
            try:
                session = self.session_manager.get_session(session_id)
                self.user_sessions[query.from_user.id] = session_id
                await query.edit_message_text(
                    f"✅ Selected session *{session.name}*", parse_mode="Markdown"
                )
            except SessionNotFoundError:
                await query.edit_message_text("❌ Session not found.")

        elif data.startswith("delete:"):
            session_id = data.split(":")[1]
            if session_id == "cancel":
                await query.edit_message_text("Cancelled.")
            else:
                try:
                    session = self.session_manager.get_session(session_id)
                    session_name = session.name
                    await self.session_manager.destroy_session(session_id)

                    if self.user_sessions.get(query.from_user.id) == session_id:
                        self.user_sessions.pop(query.from_user.id, None)

                    await query.edit_message_text(
                        f"✅ Deleted session *{session_name}*", parse_mode="Markdown"
                    )
                except SessionNotFoundError:
                    await query.edit_message_text("❌ Session not found.")

    async def _handle_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle text messages."""
        if not update.effective_user or not update.message:
            return

        if not self._is_allowed(update.effective_user.id):
            return

        session_id = self.user_sessions.get(update.effective_user.id)
        if not session_id:
            await update.message.reply_text(
                "No session selected. Use /select or /new first."
            )
            return

        text = update.message.text
        if not text:
            return

        try:
            start_time = time.time()

            # Send acknowledgement message first
            ack_msg = self.formatter.format_acknowledgement(text)
            status_msg = await update.message.reply_text(
                f"📨 {ack_msg.text}",
                parse_mode="Markdown",
            )

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
                    emoji = "❌"
                else:
                    formatted = self.formatter.format_completion(
                        analysis, elapsed, raw_output=raw_content
                    )
                    emoji = "✅"

                # Build final message with readable output
                final_text = f"{emoji} {formatted.text}"

                # Add cleaned raw output preview
                raw_preview = self._get_readable_output(raw_content)
                if raw_preview:
                    final_text += f"\n```\n{raw_preview}\n```"

                # Truncate if needed (Telegram limit ~4096)
                if len(final_text) > 3800:
                    final_text = final_text[:3800] + "...\n```"

                # Update the status message with completion
                try:
                    await status_msg.edit_text(
                        final_text,
                        parse_mode="Markdown",
                    )
                except Exception:
                    # If edit fails, send new message
                    await update.message.reply_text(final_text, parse_mode="Markdown")
            else:
                # No output - update with simple completion
                try:
                    await status_msg.edit_text(
                        "✅ 완료 (출력 없음)",
                        parse_mode="Markdown",
                    )
                except Exception:
                    pass

        except SessionNotFoundError:
            self.user_sessions.pop(update.effective_user.id, None)
            await update.message.reply_text("❌ Session not found.")
        except Exception as e:
            logger.error(f"Failed to handle message: {e}")
            await update.message.reply_text(f"❌ Error: {e}")

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

        # Truncate if too long (Telegram has 4096 char limit)
        if len(preview) > 1500:
            preview = preview[-1500:]

        return preview

    def _get_readable_output(self, output: str, max_lines: int = 50) -> str:
        """Get readable output with ANSI codes stripped.

        Args:
            output: Raw output text (may contain ANSI codes)
            max_lines: Maximum lines to show

        Returns:
            Cleaned, readable output string
        """
        import re

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

        # Truncate if too long (Telegram limit ~4096, leave room for header)
        if len(result) > 3000:
            result = "..." + result[-3000:]

        return result

    async def _send_output(self, message, text: str) -> None:
        """Send output, splitting if necessary."""
        chunks = self._split_message(text)

        for chunk in chunks[:5]:  # Limit to 5 chunks
            await message.reply_text(f"```\n{chunk}\n```", parse_mode="Markdown")
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
        logger.info("Starting Telegram bot...")
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling()

        # Keep running
        try:
            while True:
                await asyncio.sleep(1)
        finally:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()


async def run_bot(
    token: str,
    session_manager: SessionManager,
    allowed_user_ids: Optional[list[int]] = None,
) -> None:
    """Run the Telegram bot."""
    bot = TelegramBot(token, session_manager, allowed_user_ids)
    await bot.run()
