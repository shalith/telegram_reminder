from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.config import Settings
from app.db import get_session
from app.models import Reminder, ReminderStatus
from app.recurrence import format_dt_for_user
from app.runtime import RuntimeState
from app.service import ReminderService, reminder_summary_line

logger = logging.getLogger(__name__)


class ReminderScheduler:
    def __init__(self, settings: Settings, runtime_state: RuntimeState):
        self.settings = settings
        self.runtime_state = runtime_state
        self.scheduler = BackgroundScheduler(timezone="UTC")
        self.service = ReminderService()

    def start(self) -> None:
        self.scheduler.start()
        logger.info("Scheduler started", extra={"event": "scheduler_started"})

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped", extra={"event": "scheduler_stopped"})

    def is_running(self) -> bool:
        return bool(self.scheduler.running)

    def job_count(self) -> int:
        return len(self.scheduler.get_jobs())

    def schedule_reminder(self, reminder_id: int, run_at_utc_naive: datetime | None, job_id: str) -> None:
        if run_at_utc_naive is None:
            return

        run_at_utc = run_at_utc_naive.replace(tzinfo=UTC)
        self.scheduler.add_job(
            self._execute_due_reminder,
            trigger=DateTrigger(run_date=run_at_utc),
            id=job_id,
            replace_existing=True,
            kwargs={"reminder_id": reminder_id},
            misfire_grace_time=300,
        )
        logger.info(
            "Reminder job scheduled",
            extra={"event": "reminder_job_scheduled", "reminder_id": reminder_id, "job_id": job_id},
        )

    def remove_reminder_job(self, job_id: str) -> None:
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)
            logger.info("Reminder job removed", extra={"event": "reminder_job_removed", "job_id": job_id})

    def schedule_daily_agenda(self, *, chat_id: int, timezone_name: str, hour_local: int, minute_local: int) -> None:
        self.scheduler.add_job(
            self._execute_daily_agenda,
            trigger=CronTrigger(hour=hour_local, minute=minute_local, timezone=timezone_name),
            id=self.daily_agenda_job_id(chat_id),
            replace_existing=True,
            kwargs={"chat_id": chat_id},
            misfire_grace_time=300,
        )
        logger.info(
            "Daily agenda scheduled",
            extra={"event": "daily_agenda_scheduled", "chat_id": chat_id, "job_id": self.daily_agenda_job_id(chat_id)},
        )

    def remove_daily_agenda_job(self, chat_id: int) -> None:
        job_id = self.daily_agenda_job_id(chat_id)
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)
            logger.info("Daily agenda removed", extra={"event": "daily_agenda_removed", "chat_id": chat_id, "job_id": job_id})

    def restore_pending_jobs(self) -> None:
        with get_session() as session:
            reminders = self.service.get_schedulable_reminders(session)
            for reminder in reminders:
                self.schedule_reminder(reminder.id, reminder.next_run_at_utc, reminder.job_id)
            preferences = self.service.list_daily_agenda_preferences(session)
            for pref in preferences:
                if pref.daily_agenda_hour_local is not None and pref.daily_agenda_minute_local is not None:
                    self.schedule_daily_agenda(
                        chat_id=pref.chat_id,
                        timezone_name=pref.timezone,
                        hour_local=pref.daily_agenda_hour_local,
                        minute_local=pref.daily_agenda_minute_local,
                    )
        logger.info(
            "Pending jobs restored",
            extra={"event": "jobs_restored", "status": f"{self.job_count()} jobs"},
        )

    def _execute_due_reminder(self, reminder_id: int) -> None:
        try:
            self.runtime_state.record_scheduler_activity(reminder_fired=True)
            with get_session() as session:
                reminder = self.service.get_reminder(session, reminder_id=reminder_id)
                if reminder is None:
                    logger.warning("Reminder not found", extra={"event": "reminder_missing", "reminder_id": reminder_id})
                    return
                if reminder.status not in (ReminderStatus.ACTIVE.value, ReminderStatus.PENDING_ACK.value):
                    logger.info(
                        "Reminder no longer open",
                        extra={"event": "reminder_skipped_closed", "reminder_id": reminder.id, "status": reminder.status},
                    )
                    return

                now_utc = datetime.now(UTC)

                if reminder.status == ReminderStatus.ACTIVE.value:
                    sent_message = asyncio.run(self._send_alert(reminder=reminder, retry=False))
                    if reminder.requires_ack:
                        self.service.record_ack_alert_sent(
                            session,
                            reminder=reminder,
                            fired_at_utc=now_utc,
                            message_id=sent_message.message_id,
                        )
                        self.schedule_reminder(reminder.id, reminder.next_run_at_utc, reminder.job_id)
                        logger.info(
                            "Reminder fired and is awaiting acknowledgement",
                            extra={"event": "reminder_pending_ack", "reminder_id": reminder.id},
                        )
                    else:
                        self.service.record_non_ack_delivery(
                            session,
                            reminder=reminder,
                            delivered_at_utc=now_utc,
                            message_id=sent_message.message_id,
                        )
                        self.schedule_reminder(reminder.id, reminder.next_run_at_utc, reminder.job_id)
                        logger.info(
                            "Reminder delivered",
                            extra={"event": "reminder_delivered", "reminder_id": reminder.id},
                        )
                    return

                if reminder.status == ReminderStatus.PENDING_ACK.value:
                    if reminder.attempt_count >= reminder.max_attempts:
                        self.service.mark_missed(session, reminder=reminder, missed_at_utc=now_utc)
                        self.schedule_reminder(reminder.id, reminder.next_run_at_utc, reminder.job_id)
                        logger.info(
                            "Reminder marked missed after max attempts",
                            extra={"event": "reminder_missed", "reminder_id": reminder.id},
                        )
                        return

                    sent_message = asyncio.run(self._send_alert(reminder=reminder, retry=True))
                    self.service.record_ack_alert_sent(
                        session,
                        reminder=reminder,
                        fired_at_utc=now_utc,
                        message_id=sent_message.message_id,
                    )
                    self.schedule_reminder(reminder.id, reminder.next_run_at_utc, reminder.job_id)
                    logger.info(
                        "Reminder retried for acknowledgement",
                        extra={
                            "event": "reminder_retried",
                            "reminder_id": reminder.id,
                            "status": f"attempt={reminder.attempt_count}/{reminder.max_attempts}",
                        },
                    )
        except Exception as exc:  # pragma: no cover - exercised in runtime
            self.runtime_state.record_error(str(exc))
            logger.exception("Scheduler execution failed", extra={"event": "scheduler_error", "reminder_id": reminder_id})

    def _execute_daily_agenda(self, chat_id: int) -> None:
        try:
            self.runtime_state.record_scheduler_activity(daily_agenda_sent=True)
            asyncio.run(self.send_daily_agenda(chat_id=chat_id, automatic=True))
        except Exception as exc:  # pragma: no cover - exercised in runtime
            self.runtime_state.record_error(str(exc))
            logger.exception("Daily agenda execution failed", extra={"event": "daily_agenda_error", "chat_id": chat_id})

    async def send_daily_agenda(self, *, chat_id: int, automatic: bool) -> None:
        with get_session() as session:
            pref = self.service.get_preference(session, chat_id=chat_id)
            timezone_name = pref.timezone if pref is not None else self.settings.default_timezone
            today_reminders = self.service.list_today_reminders(session, chat_id=chat_id, timezone_name=timezone_name)
            missed = self.service.list_missed_reminders(
                session,
                chat_id=chat_id,
                only_unreported=bool(pref.missed_summary_enabled) if pref is not None else False,
            )
            text = self._build_daily_agenda_text(today_reminders, missed, timezone_name, automatic=automatic)
            if missed and pref is not None and pref.missed_summary_enabled:
                self.service.mark_missed_reported(session, reminder_ids=[item.id for item in missed])

        bot = Bot(token=self.settings.telegram_bot_token)
        async with bot:
            await bot.send_message(chat_id=chat_id, text=text)
        self.runtime_state.record_outbound_message()
        logger.info("Daily agenda sent", extra={"event": "daily_agenda_sent", "chat_id": chat_id})

    async def _send_alert(self, *, reminder: Reminder, retry: bool) -> Message:
        local_time = format_dt_for_user(reminder.next_run_at_utc or datetime.now(UTC).replace(tzinfo=None), reminder.timezone)
        if retry:
            text = (
                f"🔁 Wake-up retry for reminder #{reminder.id}\n"
                f"Task: {reminder.task}\n"
                f"Originally due: {local_time}\n"
                f"Please acknowledge or snooze."
            )
        else:
            text = f"⏰ Reminder #{reminder.id}: {reminder.task}\nWhen: {local_time}"

        reply_markup = None
        if reminder.requires_ack:
            reply_markup = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("✅ Awake", callback_data=f"ack:{reminder.id}")],
                    [InlineKeyboardButton("😴 Snooze", callback_data=f"snooze:{reminder.id}")],
                ]
            )

        bot = Bot(token=self.settings.telegram_bot_token)
        async with bot:
            message = await bot.send_message(chat_id=reminder.chat_id, text=text, reply_markup=reply_markup)
        self.runtime_state.record_outbound_message()
        logger.info(
            "Reminder alert sent",
            extra={"event": "reminder_alert_sent", "chat_id": reminder.chat_id, "reminder_id": reminder.id},
        )
        return message

    def _build_daily_agenda_text(self, reminders: list[Reminder], missed: list[Reminder], timezone_name: str, *, automatic: bool) -> str:
        intro = "🗓️ Your daily agenda" if automatic else "🗓️ Here is what you have today"
        lines = [intro]
        if reminders:
            lines.append("")
            lines.append("Today:")
            for reminder in reminders:
                when_label = format_dt_for_user(reminder.next_run_at_utc, timezone_name) if reminder.next_run_at_utc else "not scheduled"
                lines.append(f"• {reminder_summary_line(reminder, when_label)}")
        else:
            lines.append("")
            lines.append("No upcoming reminders for today.")

        if missed:
            lines.append("")
            lines.append("Missed reminders:")
            for reminder in missed[:10]:
                lines.append(f"• #{reminder.id} — {reminder.task}")
        return "\n".join(lines)

    @staticmethod
    def daily_agenda_job_id(chat_id: int) -> str:
        return f"daily-agenda-{chat_id}"
