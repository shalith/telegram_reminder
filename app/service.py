from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent_schema import DeadlineOffset, PendingState
from app.assistant_features import compute_deadline_trigger_utc, local_day_bounds_utc
from app.models import ConversationState, Reminder, ReminderStatus, UserPreference
from app.recurrence import compute_next_occurrence_utc, recurrence_label


OPEN_STATUSES = (ReminderStatus.ACTIVE.value, ReminderStatus.PENDING_ACK.value)


class ReminderService:
    def create_reminder(
        self,
        session: Session,
        *,
        telegram_user_id: int,
        chat_id: int,
        task: str,
        original_text: str,
        next_run_at_utc: datetime,
        timezone_name: str,
        recurrence_type: str,
        recurrence_day_of_week: int | None,
        hour_local: int | None,
        minute_local: int | None,
        requires_ack: bool,
        retry_interval_minutes: int,
        max_attempts: int,
        group_token: str | None = None,
    ) -> Reminder:
        reminder = Reminder(
            telegram_user_id=telegram_user_id,
            chat_id=chat_id,
            task=task,
            original_text=original_text,
            next_run_at_utc=next_run_at_utc.replace(tzinfo=None),
            timezone=timezone_name,
            status=ReminderStatus.ACTIVE.value,
            job_id=f"reminder-{uuid4().hex}",
            recurrence_type=recurrence_type,
            recurrence_day_of_week=recurrence_day_of_week,
            hour_local=hour_local,
            minute_local=minute_local,
            requires_ack=requires_ack,
            retry_interval_minutes=retry_interval_minutes,
            max_attempts=max_attempts,
            attempt_count=0,
            group_token=group_token,
        )
        session.add(reminder)
        session.commit()
        session.refresh(reminder)
        return reminder

    def create_deadline_chain(
        self,
        session: Session,
        *,
        telegram_user_id: int,
        chat_id: int,
        task: str,
        original_text: str,
        deadline_utc: datetime,
        offsets: list[DeadlineOffset],
        timezone_name: str,
    ) -> list[Reminder]:
        created: list[Reminder] = []
        group_token = f"deadline-{uuid4().hex[:12]}"
        for offset in offsets:
            trigger_utc = compute_deadline_trigger_utc(deadline_utc, offset)
            if trigger_utc <= datetime.now(UTC):
                continue
            reminder = Reminder(
                telegram_user_id=telegram_user_id,
                chat_id=chat_id,
                task=f"{task} ({offset.value} {offset.unit[:-1] if offset.value == 1 else offset.unit} before deadline)",
                original_text=original_text,
                next_run_at_utc=trigger_utc.replace(tzinfo=None),
                timezone=timezone_name,
                status=ReminderStatus.ACTIVE.value,
                job_id=f"reminder-{uuid4().hex}",
                recurrence_type="once",
                recurrence_day_of_week=None,
                hour_local=None,
                minute_local=None,
                requires_ack=False,
                retry_interval_minutes=2,
                max_attempts=1,
                attempt_count=0,
                group_token=group_token,
            )
            session.add(reminder)
            created.append(reminder)
        session.commit()
        for reminder in created:
            session.refresh(reminder)
        return created

    def list_open_reminders(self, session: Session, *, chat_id: int) -> list[Reminder]:
        stmt = (
            select(Reminder)
            .where(Reminder.chat_id == chat_id, Reminder.status.in_(OPEN_STATUSES))
            .order_by(Reminder.next_run_at_utc.asc())
        )
        return list(session.scalars(stmt).all())

    def list_today_reminders(self, session: Session, *, chat_id: int, timezone_name: str) -> list[Reminder]:
        start_utc, end_utc = local_day_bounds_utc(timezone_name=timezone_name)
        stmt = (
            select(Reminder)
            .where(
                Reminder.chat_id == chat_id,
                Reminder.status.in_(OPEN_STATUSES),
                Reminder.next_run_at_utc.is_not(None),
                Reminder.next_run_at_utc >= start_utc.replace(tzinfo=None),
                Reminder.next_run_at_utc < end_utc.replace(tzinfo=None),
            )
            .order_by(Reminder.next_run_at_utc.asc())
        )
        return list(session.scalars(stmt).all())

    def list_all_reminders(self, session: Session, *, chat_id: int | None = None, status: str | None = None) -> list[Reminder]:
        stmt = select(Reminder)
        if chat_id is not None:
            stmt = stmt.where(Reminder.chat_id == chat_id)
        if status is not None:
            stmt = stmt.where(Reminder.status == status)
        stmt = stmt.order_by(Reminder.created_at.asc(), Reminder.id.asc())
        return list(session.scalars(stmt).all())

    def list_missed_reminders(self, session: Session, *, chat_id: int, only_unreported: bool = False) -> list[Reminder]:
        conditions = [Reminder.chat_id == chat_id, Reminder.status == ReminderStatus.MISSED.value]
        if only_unreported:
            conditions.append(Reminder.missed_reported.is_(False))
        stmt = select(Reminder).where(*conditions).order_by(Reminder.updated_at.desc())
        return list(session.scalars(stmt).all())

    def mark_missed_reported(self, session: Session, *, reminder_ids: list[int]) -> None:
        if not reminder_ids:
            return
        reminders = list(session.scalars(select(Reminder).where(Reminder.id.in_(reminder_ids))).all())
        for reminder in reminders:
            reminder.missed_reported = True
            reminder.updated_at = utcnow_naive()
        session.commit()

    def get_reminder(self, session: Session, *, reminder_id: int) -> Reminder | None:
        return session.get(Reminder, reminder_id)

    def delete_reminder(self, session: Session, *, chat_id: int, reminder_id: int) -> Reminder | None:
        reminder = session.get(Reminder, reminder_id)
        if reminder is None or reminder.chat_id != chat_id:
            return None

        reminder.status = ReminderStatus.CANCELLED.value
        reminder.next_run_at_utc = None
        reminder.updated_at = utcnow_naive()
        session.commit()
        session.refresh(reminder)
        return reminder

    def get_schedulable_reminders(self, session: Session) -> list[Reminder]:
        stmt = select(Reminder).where(
            Reminder.status.in_(OPEN_STATUSES),
            Reminder.next_run_at_utc.is_not(None),
        )
        return list(session.scalars(stmt).all())

    def record_ack_alert_sent(
        self,
        session: Session,
        *,
        reminder: Reminder,
        fired_at_utc: datetime,
        message_id: int,
    ) -> Reminder:
        reminder.status = ReminderStatus.PENDING_ACK.value
        reminder.attempt_count += 1
        reminder.last_message_id = message_id
        reminder.last_triggered_at_utc = fired_at_utc.replace(tzinfo=None)
        reminder.next_run_at_utc = (fired_at_utc + timedelta(minutes=reminder.retry_interval_minutes)).replace(
            tzinfo=None
        )
        reminder.updated_at = utcnow_naive()
        session.commit()
        session.refresh(reminder)
        return reminder

    def record_non_ack_delivery(
        self,
        session: Session,
        *,
        reminder: Reminder,
        delivered_at_utc: datetime,
        message_id: int,
    ) -> Reminder:
        reminder.last_message_id = message_id
        reminder.last_triggered_at_utc = delivered_at_utc.replace(tzinfo=None)
        reminder.attempt_count = 0
        next_run = self._compute_next_occurrence(reminder, after_utc=delivered_at_utc)
        if next_run is None:
            reminder.status = ReminderStatus.COMPLETED.value
            reminder.next_run_at_utc = None
            reminder.completed_at = delivered_at_utc.replace(tzinfo=None)
        else:
            reminder.status = ReminderStatus.ACTIVE.value
            reminder.next_run_at_utc = next_run.replace(tzinfo=None)
        reminder.updated_at = utcnow_naive()
        session.commit()
        session.refresh(reminder)
        return reminder

    def acknowledge_reminder(
        self,
        session: Session,
        *,
        reminder: Reminder,
        acked_at_utc: datetime,
    ) -> Reminder:
        reminder.acked_at = acked_at_utc.replace(tzinfo=None)
        reminder.attempt_count = 0
        next_run = self._compute_next_occurrence(reminder, after_utc=acked_at_utc)
        if next_run is None:
            reminder.status = ReminderStatus.COMPLETED.value
            reminder.next_run_at_utc = None
            reminder.completed_at = acked_at_utc.replace(tzinfo=None)
        else:
            reminder.status = ReminderStatus.ACTIVE.value
            reminder.next_run_at_utc = next_run.replace(tzinfo=None)
        reminder.updated_at = utcnow_naive()
        session.commit()
        session.refresh(reminder)
        return reminder

    def snooze_reminder(
        self,
        session: Session,
        *,
        reminder: Reminder,
        snooze_until_utc: datetime,
    ) -> Reminder:
        reminder.status = ReminderStatus.ACTIVE.value
        reminder.attempt_count = 0
        reminder.next_run_at_utc = snooze_until_utc.replace(tzinfo=None)
        reminder.updated_at = utcnow_naive()
        session.commit()
        session.refresh(reminder)
        return reminder

    def mark_missed(self, session: Session, *, reminder: Reminder, missed_at_utc: datetime) -> Reminder:
        reminder.attempt_count = 0
        reminder.missed_reported = False
        next_run = self._compute_next_occurrence(reminder, after_utc=missed_at_utc)
        if next_run is None:
            reminder.status = ReminderStatus.MISSED.value
            reminder.next_run_at_utc = None
            reminder.completed_at = missed_at_utc.replace(tzinfo=None)
        else:
            reminder.status = ReminderStatus.ACTIVE.value
            reminder.next_run_at_utc = next_run.replace(tzinfo=None)
        reminder.updated_at = utcnow_naive()
        session.commit()
        session.refresh(reminder)
        return reminder

    def update_reminder_schedule(
        self,
        session: Session,
        *,
        reminder: Reminder,
        original_text: str,
        next_run_at_utc: datetime,
        recurrence_type: str,
        recurrence_day_of_week: int | None,
        hour_local: int | None,
        minute_local: int | None,
        requires_ack: bool,
        retry_interval_minutes: int,
        max_attempts: int,
    ) -> Reminder:
        reminder.original_text = original_text
        reminder.next_run_at_utc = next_run_at_utc.replace(tzinfo=None)
        reminder.recurrence_type = recurrence_type
        reminder.recurrence_day_of_week = recurrence_day_of_week
        reminder.hour_local = hour_local
        reminder.minute_local = minute_local
        reminder.requires_ack = requires_ack
        reminder.retry_interval_minutes = retry_interval_minutes
        reminder.max_attempts = max_attempts
        reminder.attempt_count = 0
        reminder.status = ReminderStatus.ACTIVE.value
        reminder.updated_at = utcnow_naive()
        session.commit()
        session.refresh(reminder)
        return reminder

    def find_open_reminder(
        self,
        session: Session,
        *,
        chat_id: int,
        target_reminder_id: int | None = None,
        target_hint: str | None = None,
    ) -> tuple[Reminder | None, str | None]:
        reminders = self.list_open_reminders(session, chat_id=chat_id)
        if not reminders:
            return None, "You don't have any open reminders right now."

        if target_reminder_id is not None:
            for reminder in reminders:
                if reminder.id == target_reminder_id:
                    return reminder, None
            return None, f"I couldn't find an open reminder with ID {target_reminder_id}."

        hint = (target_hint or "").strip().lower()
        if not hint:
            if len(reminders) == 1:
                return reminders[0], None
            return None, "I need to know which reminder you mean."

        ranked: list[tuple[float, Reminder]] = []
        for reminder in reminders:
            haystack = self._target_text(reminder)
            score = similarity_score(hint, haystack)
            if hint in haystack:
                score += 0.35
            ranked.append((score, reminder))

        ranked.sort(key=lambda item: item[0], reverse=True)
        if not ranked or ranked[0][0] < 0.45:
            return None, "I couldn't match that to one of your open reminders."
        if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 0.08:
            return None, "That matches more than one reminder. Please use /list and tell me the reminder ID."
        return ranked[0][1], None

    def _target_text(self, reminder: Reminder) -> str:
        parts = [
            reminder.task.lower(),
            recurrence_label(reminder).lower(),
        ]
        if reminder.requires_ack:
            parts.append("wake up")
        return " ".join(parts)

    def get_pending_state(self, session: Session, *, chat_id: int) -> PendingState | None:
        stmt = select(ConversationState).where(ConversationState.chat_id == chat_id)
        state = session.scalar(stmt)
        if state is None:
            return None
        try:
            return PendingState.model_validate_json(state.state_json)
        except Exception:
            return None

    def save_pending_state(
        self,
        session: Session,
        *,
        chat_id: int,
        telegram_user_id: int,
        pending_state: PendingState,
    ) -> None:
        stmt = select(ConversationState).where(ConversationState.chat_id == chat_id)
        state = session.scalar(stmt)
        payload = pending_state.model_dump_json()
        if state is None:
            state = ConversationState(
                chat_id=chat_id,
                telegram_user_id=telegram_user_id,
                pending_intent=pending_state.intent,
                state_json=payload,
            )
            session.add(state)
        else:
            state.telegram_user_id = telegram_user_id
            state.pending_intent = pending_state.intent
            state.state_json = payload
            state.updated_at = utcnow_naive()
        session.commit()

    def clear_pending_state(self, session: Session, *, chat_id: int) -> None:
        stmt = select(ConversationState).where(ConversationState.chat_id == chat_id)
        state = session.scalar(stmt)
        if state is not None:
            session.delete(state)
            session.commit()

    def get_or_create_preferences(
        self,
        session: Session,
        *,
        chat_id: int,
        telegram_user_id: int,
        timezone_name: str,
    ) -> UserPreference:
        stmt = select(UserPreference).where(UserPreference.chat_id == chat_id)
        pref = session.scalar(stmt)
        if pref is None:
            pref = UserPreference(
                chat_id=chat_id,
                telegram_user_id=telegram_user_id,
                timezone=timezone_name,
            )
            session.add(pref)
            session.commit()
            session.refresh(pref)
            return pref
        return pref

    def get_preference(self, session: Session, *, chat_id: int) -> UserPreference | None:
        stmt = select(UserPreference).where(UserPreference.chat_id == chat_id)
        return session.scalar(stmt)

    def list_daily_agenda_preferences(self, session: Session) -> list[UserPreference]:
        stmt = select(UserPreference).where(
            UserPreference.daily_agenda_enabled.is_(True),
            UserPreference.daily_agenda_hour_local.is_not(None),
            UserPreference.daily_agenda_minute_local.is_not(None),
        )
        return list(session.scalars(stmt).all())

    def update_preferences(
        self,
        session: Session,
        *,
        preference: UserPreference,
        updates: dict[str, object],
    ) -> UserPreference:
        for key, value in updates.items():
            setattr(preference, key, value)
        preference.updated_at = utcnow_naive()
        session.commit()
        session.refresh(preference)
        return preference

    def format_preferences_summary(self, preference: UserPreference) -> str:
        agenda = "off"
        if preference.daily_agenda_enabled and preference.daily_agenda_hour_local is not None and preference.daily_agenda_minute_local is not None:
            agenda = f"daily at {preference.daily_agenda_hour_local:02d}:{preference.daily_agenda_minute_local:02d}"
        return (
            f"Preferences:\n"
            f"• Snooze: {preference.default_snooze_minutes} min\n"
            f"• Wake-up retry interval: {preference.wakeup_retry_interval_minutes} min\n"
            f"• Wake-up max attempts: {preference.wakeup_max_attempts}\n"
            f"• Daily agenda: {agenda}\n"
            f"• Missed summary: {'on' if preference.missed_summary_enabled else 'off'}"
        )

    def _compute_next_occurrence(self, reminder: Reminder, *, after_utc: datetime) -> datetime | None:
        return compute_next_occurrence_utc(
            recurrence_type=reminder.recurrence_type,
            timezone_name=reminder.timezone,
            hour_local=reminder.hour_local,
            minute_local=reminder.minute_local,
            recurrence_day_of_week=reminder.recurrence_day_of_week,
            after_utc=after_utc,
        )



def similarity_score(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()



def utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)



def reminder_summary_line(reminder: Reminder, when_label: str) -> str:
    ack_part = " | ack required" if reminder.requires_ack else ""
    pending_part = " | waiting for ack" if reminder.status == ReminderStatus.PENDING_ACK.value else ""
    recurrence_part = recurrence_label(reminder)
    return f"#{reminder.id} — {when_label} — {reminder.task} ({recurrence_part}{ack_part}{pending_part})"
