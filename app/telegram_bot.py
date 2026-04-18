from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import Application, ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from app.config import Settings
from app.db import get_session
from app.models import ReminderStatus
from app.recurrence import format_dt_for_user
from app.runtime import RuntimeState
from app.phase9_4 import ExecutionGuard
from app.scheduler import ReminderScheduler
from app.service import ReminderService, reminder_summary_line
from app.services.interpretation_service import InterpretationService

logger = logging.getLogger(__name__)

HELP_TEXT = """Try these examples:
• Remind me tomorrow at 7 PM to pay rent
• Wake me up every weekday at 6 AM
• Show my reminders
• Move my wake-up to 7 AM
• What do I have today
• Set my snooze to 10 minutes
• My report deadline is April 30 at 5 PM, remind me 7 days before and 2 hours before

Commands:
/list - show your open reminders
/today - show today's agenda
/prefs - show your preferences
/delete <id> - cancel a reminder
/help - show this help message"""


class TelegramReminderBot:
    def __init__(self, settings: Settings, reminder_scheduler: ReminderScheduler, runtime_state: RuntimeState):
        self.settings = settings
        self.scheduler = reminder_scheduler
        self.service = ReminderService()
        self.runtime_state = runtime_state
        self.interpretation_service = InterpretationService(settings, reminder_scheduler, runtime_state)
        self.execution_guard = ExecutionGuard()

    def build(self) -> Application:
        application = ApplicationBuilder().token(self.settings.telegram_bot_token).post_init(self._post_init).build()
        application.add_handler(CommandHandler("start", self.start_command))
        application.add_handler(CommandHandler("help", self.help_command))
        application.add_handler(CommandHandler("list", self.list_command))
        application.add_handler(CommandHandler("today", self.today_command))
        application.add_handler(CommandHandler("prefs", self.prefs_command))
        application.add_handler(CommandHandler("delete", self.delete_command))
        application.add_handler(CallbackQueryHandler(self.handle_callback_query, pattern=r"^(ack|snooze|resolve|confirm):"))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_message))
        application.add_error_handler(self.handle_error)
        return application

    async def _post_init(self, application: Application) -> None:
        self.runtime_state.mark_bot_started()
        bot_me = await application.bot.get_me()
        logger.info("Telegram bot started", extra={"event": "telegram_bot_started", "status": bot_me.username})

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None or update.effective_chat is None or update.effective_user is None:
            return
        with get_session() as session:
            self.service.get_or_create_preferences(
                session,
                chat_id=update.effective_chat.id,
                telegram_user_id=update.effective_user.id,
                timezone_name=self.settings.default_timezone,
            )
        await self._reply_text(update, "Hello! I am your reminder bot.\n\n" + HELP_TEXT)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None:
            return
        await self._reply_text(update, HELP_TEXT)

    async def list_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None or update.effective_chat is None:
            return
        await self._reply_text(update, await self._render_list(chat_id=update.effective_chat.id))

    async def today_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None or update.effective_chat is None:
            return
        await self._reply_text(update, await self._render_today(chat_id=update.effective_chat.id))

    async def prefs_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None or update.effective_chat is None or update.effective_user is None:
            return
        await self._reply_text(
            update,
            await self._render_preferences(chat_id=update.effective_chat.id, telegram_user_id=update.effective_user.id),
        )

    async def delete_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None or update.effective_chat is None or update.effective_user is None:
            return
        if not context.args:
            await self._reply_text(update, "Usage: /delete <reminder_id>")
            return
        raw_id = context.args[0].strip()
        if not raw_id.isdigit():
            await self._reply_text(update, "Reminder ID must be a number. Example: /delete 3")
            return
        with get_session() as session:
            plan = self.interpretation_service.handle_user_message(
                session,
                chat_id=update.effective_chat.id,
                telegram_user_id=update.effective_user.id,
                message_text=f"cancel reminder #{raw_id}",
            )
        await self._reply_text(update, plan.text, reply_markup=plan.reply_markup)

    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None or update.effective_chat is None or update.effective_user is None:
            return
        incoming_text = update.message.text or ""
        self.runtime_state.record_inbound_message()
        lowered = " ".join(incoming_text.strip().lower().split())
        should_guard = lowered.startswith((
            "remind me", "wake me up", "wake up me", "wake up", "list", "show", "what", "update", "delete", "cancel", "remove",
        )) or "tomorrow" in lowered or "today" in lowered
        if should_guard and self.execution_guard.should_skip_repeated_message(chat_id=update.effective_chat.id, message_text=incoming_text):
            await self._reply_text(update, "I already received that same request just now. If you meant something different, please rephrase it a little.")
            return
        try:
            with get_session() as session:
                plan = self.interpretation_service.handle_user_message(
                    session,
                    chat_id=update.effective_chat.id,
                    telegram_user_id=update.effective_user.id,
                    message_text=incoming_text,
                )
            await self._reply_text(update, plan.text, reply_markup=plan.reply_markup)
        except Exception as exc:
            self.runtime_state.record_error(str(exc))
            logger.exception('Text message handling failed', extra={'event': 'text_message_failed'})
            await self._reply_text(update, 'Sorry — that request failed unexpectedly. Nothing new was scheduled. Please try again with a fresh message.')


    async def handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None or update.effective_chat is None or update.effective_user is None:
            return

        self.runtime_state.record_callback()
        callback_data = query.data or ""
        if not self.execution_guard.mark_callback_started(
            callback_query_id=query.id,
            callback_data=callback_data,
            chat_id=update.effective_chat.id,
        ):
            cached = self.execution_guard.get_callback_result(
                callback_query_id=query.id,
                callback_data=callback_data,
                chat_id=update.effective_chat.id,
            )
            await query.answer(cached or 'Already handled.', show_alert=False)
            return

        await query.answer()
        parts = callback_data.split(":")
        action = parts[0]

        try:
            if action == "resolve":
                if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
                    if query.message is not None:
                        await query.message.reply_text("That selection payload is invalid.")
                        self.runtime_state.record_outbound_message()
                    self.execution_guard.remember_callback_result(callback_query_id=query.id, callback_data=callback_data, chat_id=update.effective_chat.id, response_text="That selection payload is invalid.")
                    return
                ai_run_id = int(parts[1])
                reminder_id = int(parts[2])
                with get_session() as session:
                    text = self.interpretation_service.handle_resolution_choice(
                        session,
                        ai_run_id=ai_run_id,
                        reminder_id=reminder_id,
                        chat_id=update.effective_chat.id,
                        telegram_user_id=update.effective_user.id,
                    )
                if query.message is not None:
                    await self._safe_clear_callback_markup(query)
                    await query.message.reply_text(text)
                    self.runtime_state.record_outbound_message()
                self.execution_guard.remember_callback_result(callback_query_id=query.id, callback_data=callback_data, chat_id=update.effective_chat.id, response_text=text)
                return

            if action == "confirm":
                choice = parts[1] if len(parts) >= 2 else ""
                with get_session() as session:
                    text = self.interpretation_service.handle_confirmation_choice(
                        session,
                        choice=choice,
                        chat_id=update.effective_chat.id,
                        telegram_user_id=update.effective_user.id,
                    )
                if query.message is not None:
                    await self._safe_clear_callback_markup(query)
                    await query.message.reply_text(text)
                    self.runtime_state.record_outbound_message()
                self.execution_guard.remember_callback_result(callback_query_id=query.id, callback_data=callback_data, chat_id=update.effective_chat.id, response_text=text)
                return

            if len(parts) < 2 or not parts[1].isdigit():
                if query.message is not None:
                    await query.message.reply_text("That action payload is invalid.")
                    self.runtime_state.record_outbound_message()
                return

            reminder_id = int(parts[1])
            with get_session() as session:
                reminder = self.service.get_reminder(session, reminder_id=reminder_id)
                preference = self.service.get_or_create_preferences(
                    session,
                    chat_id=update.effective_chat.id,
                    telegram_user_id=update.effective_user.id,
                    timezone_name=self.settings.default_timezone,
                )
                if reminder is None or reminder.chat_id != update.effective_chat.id:
                    if query.message is not None:
                        await query.message.reply_text("I couldn't find that reminder.")
                        self.runtime_state.record_outbound_message()
                    return

                if action == "ack":
                    if reminder.status != ReminderStatus.PENDING_ACK.value:
                        if query.message is not None:
                            await query.message.reply_text("That reminder is not waiting for acknowledgement anymore.")
                            self.runtime_state.record_outbound_message()
                        return
                    self.scheduler.remove_reminder_job(reminder.job_id)
                    updated = self.service.acknowledge_reminder(session, reminder=reminder, acked_at_utc=datetime.now(UTC))
                    self.scheduler.schedule_reminder(updated.id, updated.next_run_at_utc, updated.job_id)
                    if query.message is not None:
                        await self._safe_clear_callback_markup(query)
                        if updated.status == ReminderStatus.ACTIVE.value and updated.next_run_at_utc is not None:
                            next_run = format_dt_for_user(updated.next_run_at_utc, updated.timezone)
                            await query.message.reply_text(
                                f"Got it — reminder #{updated.id} acknowledged. Next wake-up: {next_run}."
                            )
                        else:
                            await query.message.reply_text(f"Got it — reminder #{updated.id} acknowledged.")
                        self.runtime_state.record_outbound_message()
                    self.execution_guard.remember_callback_result(callback_query_id=query.id, callback_data=callback_data, chat_id=update.effective_chat.id, response_text=f"Got it — reminder #{updated.id} acknowledged.")
                    return

                if action == "snooze":
                    self.scheduler.remove_reminder_job(reminder.job_id)
                    snooze_until = datetime.now(UTC) + timedelta(minutes=preference.default_snooze_minutes)
                    updated = self.service.snooze_reminder(session, reminder=reminder, snooze_until_utc=snooze_until)
                    self.scheduler.schedule_reminder(updated.id, updated.next_run_at_utc, updated.job_id)
                    next_run = (
                        format_dt_for_user(updated.next_run_at_utc, updated.timezone)
                        if updated.next_run_at_utc
                        else "later"
                    )
                    result_text = f"Snoozed reminder #{updated.id} for {preference.default_snooze_minutes} minutes. Next alert: {next_run}."
                    if query.message is not None:
                        await self._safe_clear_callback_markup(query)
                        await query.message.reply_text(result_text)
                        self.runtime_state.record_outbound_message()
                    self.execution_guard.remember_callback_result(callback_query_id=query.id, callback_data=callback_data, chat_id=update.effective_chat.id, response_text=result_text)
                    return

            if query.message is not None:
                await query.message.reply_text("That action is not supported.")
                self.runtime_state.record_outbound_message()
                self.execution_guard.remember_callback_result(callback_query_id=query.id, callback_data=callback_data, chat_id=update.effective_chat.id, response_text="That action is not supported.")
        except Exception:
            self.runtime_state.record_error(f'callback_failed:{callback_data}')
            logger.exception("Callback query handling failed", extra={"event": "callback_query_failed", "callback_data": callback_data})
            if query.message is not None:
                error_text = "Sorry — that button action failed safely, and no duplicate action was taken. Please try the request again."
                await query.message.reply_text(error_text)
                self.runtime_state.record_outbound_message()
                self.execution_guard.remember_callback_result(callback_query_id=query.id, callback_data=callback_data, chat_id=update.effective_chat.id, response_text=error_text)

    async def _safe_clear_callback_markup(self, query) -> None:
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except TelegramError:
            logger.warning("Failed to clear callback markup", extra={"event": "clear_callback_markup_failed"})

    async def handle_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.exception("Unhandled Telegram application error", exc_info=context.error)

    async def _reply_text(self, update: Update, text: str, reply_markup=None) -> None:
        if update.message is None:
            return
        await update.message.reply_text(text, reply_markup=reply_markup)
        self.runtime_state.record_outbound_message()

    async def _render_list(self, *, chat_id: int) -> str:
        with get_session() as session:
            reminders = self.service.list_open_reminders(session, chat_id=chat_id)
        if not reminders:
            return "You do not have any open reminders right now."
        lines = ["Open reminders:"]
        for reminder in reminders:
            when_label = (
                format_dt_for_user(reminder.next_run_at_utc, reminder.timezone)
                if reminder.next_run_at_utc
                else "not scheduled"
            )
            lines.append(f"• {reminder_summary_line(reminder, when_label)}")
        return "\n".join(lines)

    async def _render_today(self, *, chat_id: int) -> str:
        with get_session() as session:
            pref = self.service.get_preference(session, chat_id=chat_id)
            timezone_name = pref.timezone if pref is not None else self.settings.default_timezone
            reminders = self.service.list_today_reminders(session, chat_id=chat_id, timezone_name=timezone_name)
        if not reminders:
            return "You have no upcoming reminders for today."
        lines = ["Today's agenda:"]
        for reminder in reminders:
            when_label = (
                format_dt_for_user(reminder.next_run_at_utc, reminder.timezone)
                if reminder.next_run_at_utc
                else "not scheduled"
            )
            lines.append(f"• {reminder_summary_line(reminder, when_label)}")
        return "\n".join(lines)

    async def _render_preferences(self, *, chat_id: int, telegram_user_id: int) -> str:
        with get_session() as session:
            pref = self.service.get_or_create_preferences(
                session,
                chat_id=chat_id,
                telegram_user_id=telegram_user_id,
                timezone_name=self.settings.default_timezone,
            )
            return self.service.format_preferences_summary(pref)
