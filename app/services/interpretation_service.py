from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from telegram import InlineKeyboardMarkup

from app.ai.checker import InterpretationChecker
from app.ai.interpreter import StructuredInterpreter
from app.ai.schemas import FollowUp, InterpretationEnvelope, PendingConversationState
from app.config import Settings
from app.models import ConversationState
from app.repositories.ai_run_repo import AiRunRepository
from app.service import ReminderService
from app.services.audit_service import AuditService
from app.services.duplicate_detection_service import DuplicateDetectionService
from app.services.target_resolution_service import TargetResolutionService
from app.tools.create_reminder import CreateReminderTool
from app.tools.deadline_chain import DeadlineChainTool
from app.tools.delete_reminder import DeleteReminderTool
from app.tools.list_reminders import ListRemindersTool
from app.tools.missed_summary import MissedSummaryTool
from app.tools.set_preferences import SetPreferencesTool
from app.tools.today_agenda import TodayAgendaTool
from app.tools.update_reminder import UpdateReminderTool


@dataclass(slots=True)
class BotResponsePlan:
    text: str
    reply_markup: InlineKeyboardMarkup | None = None


class InterpretationService:
    def __init__(self, settings: Settings, scheduler, runtime_state):
        self.settings = settings
        self.scheduler = scheduler
        self.runtime_state = runtime_state
        self.reminder_service = ReminderService()
        self.interpreter = StructuredInterpreter(settings)
        self.checker = InterpretationChecker()
        self.audit = AuditService()
        self.ai_run_repo = AiRunRepository()
        self.target_resolution = TargetResolutionService()
        self.duplicates = DuplicateDetectionService()
        self.create_tool = CreateReminderTool(self.reminder_service)
        self.list_tool = ListRemindersTool(self.reminder_service)
        self.update_tool = UpdateReminderTool(self.reminder_service)
        self.delete_tool = DeleteReminderTool(self.reminder_service)
        self.today_tool = TodayAgendaTool(self.reminder_service)
        self.pref_tool = SetPreferencesTool(self.reminder_service)
        self.deadline_tool = DeadlineChainTool(self.reminder_service)
        self.missed_tool = MissedSummaryTool(self.reminder_service)

    def handle_user_message(self, session, *, chat_id: int, telegram_user_id: int, message_text: str) -> BotResponsePlan:
        pref = self.reminder_service.get_or_create_preferences(
            session,
            chat_id=chat_id,
            telegram_user_id=telegram_user_id,
            timezone_name=self.settings.default_timezone,
        )
        open_reminders = self.reminder_service.list_open_reminders(session, chat_id=chat_id)
        pending_state = self._get_pending_state(session, chat_id=chat_id)
        interpreter_result = self.interpreter.interpret(
            message_text=message_text,
            timezone_name=pref.timezone,
            pending_state=pending_state,
            open_reminders=open_reminders,
            preference_snapshot=self.reminder_service.format_preferences_summary(pref),
        )
        envelope = interpreter_result.envelope
        if pending_state is not None and envelope.action == "clarify":
            envelope = self._merge_pending_state(pending_state, message_text)
        checker_result = self.checker.check(envelope=envelope, open_reminders=open_reminders)

        ai_run = self.audit.record_ai_run(
            session,
            user_id=telegram_user_id,
            chat_id=chat_id,
            message_text=message_text,
            system_prompt_version="phase6_v1",
            model_name=interpreter_result.model_name,
            raw_response_text=interpreter_result.raw_response_text,
            parsed_json=envelope.model_dump_json(),
            validation_ok=interpreter_result.validation_ok,
            checker_ok=checker_result.ok,
            final_action=envelope.action,
            confidence=checker_result.confidence,
            error_code=None if interpreter_result.error_message is None else "interpreter_error",
            error_message=interpreter_result.error_message,
        )

        if checker_result.follow_up_text or envelope.follow_up.needed:
            follow_up_text = checker_result.follow_up_text or envelope.follow_up.question or "I need a bit more information."
            pending = PendingConversationState(
                action=envelope.action,
                reminder=envelope.reminder,
                target=envelope.target,
                preferences=envelope.preferences,
                follow_up=FollowUp(
                    needed=True,
                    question=follow_up_text,
                    missing_fields=checker_result.issues or envelope.follow_up.missing_fields,
                ),
                user_message_summary=envelope.user_message_summary,
                deadline_offsets=envelope.deadline_offsets,
            )
            self._save_pending_state(session, chat_id=chat_id, telegram_user_id=telegram_user_id, state=pending)
            self.audit.record_action(
                session,
                user_id=telegram_user_id,
                reminder_id=None,
                action_name=envelope.action,
                action_args_json=pending.model_dump_json(),
                executor_result_json=json.dumps({"follow_up": follow_up_text}),
                status="follow_up",
            )
            return BotResponsePlan(text=follow_up_text)

        self._clear_pending_state(session, chat_id=chat_id)

        if envelope.action == "help":
            return BotResponsePlan(
                text=(
                    "Try things like:\n"
                    "• Remind me tomorrow at 7 PM to pay rent\n"
                    "• Wake me up every weekday at 6 AM\n"
                    "• Show my reminders\n"
                    "• Move my wake-up to 7 AM\n"
                    "• What do I have today\n"
                    "• Set my snooze to 10 minutes"
                )
            )

        if envelope.action == "list_reminders":
            return BotResponsePlan(text=self.list_tool.execute(session, chat_id=chat_id))

        if envelope.action == "today_agenda":
            return BotResponsePlan(text=self.today_tool.execute(session, chat_id=chat_id, timezone_name=pref.timezone))

        if envelope.action == "missed_summary":
            return BotResponsePlan(text=self.missed_tool.execute(session, chat_id=chat_id))

        if envelope.action == "set_preferences":
            text = self.pref_tool.execute(
                session,
                scheduler=self.scheduler,
                preference=pref,
                preferences_patch=envelope.preferences,
                timezone_name=pref.timezone,
            )
            self.audit.record_action(
                session,
                user_id=telegram_user_id,
                reminder_id=None,
                action_name=envelope.action,
                action_args_json=envelope.model_dump_json(),
                executor_result_json=json.dumps({"text": text}),
                status="success",
            )
            return BotResponsePlan(text=text)

        if envelope.action == "deadline_chain":
            reminders, text = self.deadline_tool.execute(
                session,
                scheduler=self.scheduler,
                incoming_text=message_text,
                telegram_user_id=telegram_user_id,
                chat_id=chat_id,
                timezone_name=pref.timezone,
                task=envelope.reminder.task or "deadline",
                deadline_phrase=envelope.reminder.datetime_text or "",
                offsets=envelope.deadline_offsets,
            )
            self.audit.record_action(
                session,
                user_id=telegram_user_id,
                reminder_id=reminders[0].id if reminders else None,
                action_name=envelope.action,
                action_args_json=envelope.model_dump_json(),
                executor_result_json=json.dumps({"text": text}),
                status="success" if reminders else "error",
            )
            return BotResponsePlan(text=text)

        if envelope.action == "create_reminder":
            duplicates = self.duplicates.find_possible_duplicates(
                reminders=open_reminders,
                task=envelope.reminder.task or "",
                due_repr=envelope.reminder.datetime_text or "",
                recurrence=envelope.reminder.recurrence_text,
            )
            if duplicates:
                return BotResponsePlan(
                    text="That looks similar to an existing reminder. Say it again with a clearer time, or use /list to check first."
                )
            reminder, status = self.create_tool.execute(
                session,
                scheduler=self.scheduler,
                incoming_text=message_text,
                telegram_user_id=telegram_user_id,
                chat_id=chat_id,
                timezone_name=pref.timezone,
                task=envelope.reminder.task or "",
                time_phrase=envelope.reminder.datetime_text or "",
                requires_ack=bool(envelope.reminder.requires_ack),
                retry_interval_minutes=pref.wakeup_retry_interval_minutes,
                max_attempts=pref.wakeup_max_attempts,
                source_mode="llm" if interpreter_result.model_name != "rule-fallback" else "rule",
                interpretation_json=envelope.model_dump_json(),
                target_selector_json=envelope.target.model_dump_json(),
                ai_confidence=checker_result.confidence,
            )
            if reminder is None:
                self.audit.record_action(
                    session,
                    user_id=telegram_user_id,
                    reminder_id=None,
                    action_name=envelope.action,
                    action_args_json=envelope.model_dump_json(),
                    executor_result_json=json.dumps({"status": status}),
                    status="error",
                )
                return BotResponsePlan(text=status)
            text = f"Okay — I created reminder #{reminder.id}: {reminder.task}."
            if reminder.requires_ack:
                text += " This wake-up reminder will repeat until you acknowledge it."
            self.audit.record_action(
                session,
                user_id=telegram_user_id,
                reminder_id=reminder.id,
                action_name=envelope.action,
                action_args_json=envelope.model_dump_json(),
                executor_result_json=json.dumps({"text": text}),
                status="success",
            )
            return BotResponsePlan(text=text)

        if envelope.action in {"update_reminder", "delete_reminder"}:
            resolution = self.target_resolution.resolve(
                session=session,
                ai_run_id=ai_run.id,
                action_name=envelope.action,
                selector_text=envelope.target.selector_text,
                reminder_id=envelope.target.reminder_id,
                reminders=open_reminders,
            )
            if resolution.status == "none":
                return BotResponsePlan(text=resolution.message or "I couldn't find that reminder.")
            if resolution.status == "ambiguous":
                if not self.settings.ai_enable_resolution_buttons or not resolution.candidates:
                    return BotResponsePlan(text=resolution.message or "That matches more than one reminder.")
                keyboard = self.target_resolution.build_keyboard(ai_run_id=ai_run.id, candidates=resolution.candidates)
                return BotResponsePlan(text=resolution.message or "Please choose a reminder.", reply_markup=keyboard)
            target = resolution.selected
            assert target is not None
            if envelope.action == "delete_reminder":
                deleted, status = self.delete_tool.execute(session, scheduler=self.scheduler, chat_id=chat_id, reminder=target)
                if deleted is None:
                    return BotResponsePlan(text=status)
                text = f"Cancelled reminder #{deleted.id}: {deleted.task}"
                self.audit.record_action(
                    session,
                    user_id=telegram_user_id,
                    reminder_id=deleted.id,
                    action_name=envelope.action,
                    action_args_json=envelope.model_dump_json(),
                    executor_result_json=json.dumps({"text": text}),
                    status="success",
                )
                return BotResponsePlan(text=text)
            updated, status = self.update_tool.execute(
                session,
                scheduler=self.scheduler,
                reminder=target,
                incoming_text=message_text,
                timezone_name=pref.timezone,
                time_phrase=envelope.reminder.datetime_text or "",
                retry_interval_minutes=pref.wakeup_retry_interval_minutes,
                max_attempts=pref.wakeup_max_attempts,
                source_mode="llm" if interpreter_result.model_name != "rule-fallback" else "rule",
                interpretation_json=envelope.model_dump_json(),
                target_selector_json=envelope.target.model_dump_json(),
                ai_confidence=checker_result.confidence,
            )
            if updated is None:
                return BotResponsePlan(text=status)
            text = f"Updated reminder #{updated.id}."
            self.audit.record_action(
                session,
                user_id=telegram_user_id,
                reminder_id=updated.id,
                action_name=envelope.action,
                action_args_json=envelope.model_dump_json(),
                executor_result_json=json.dumps({"text": text}),
                status="success",
            )
            return BotResponsePlan(text=text)

        return BotResponsePlan(text="I couldn't understand that. Try /help.")

    def handle_resolution_choice(self, session, *, ai_run_id: int, reminder_id: int, chat_id: int, telegram_user_id: int) -> str:
        pref = self.reminder_service.get_or_create_preferences(
            session,
            chat_id=chat_id,
            telegram_user_id=telegram_user_id,
            timezone_name=self.settings.default_timezone,
        )
        ai_run = self.ai_run_repo.get_by_id(session, ai_run_id)
        if ai_run is None or not ai_run.parsed_json:
            return "That selection has expired. Please try again."
        envelope = InterpretationEnvelope.model_validate_json(ai_run.parsed_json)
        reminder = self.reminder_service.get_reminder(session, reminder_id=reminder_id)
        if reminder is None or reminder.chat_id != chat_id:
            return "I couldn't find that reminder anymore."
        self.target_resolution.repo.mark_selected(session, ai_run_id=ai_run_id, reminder_id=reminder_id)
        if envelope.action == "delete_reminder":
            deleted, status = self.delete_tool.execute(session, scheduler=self.scheduler, chat_id=chat_id, reminder=reminder)
            return status if deleted is None else f"Cancelled reminder #{deleted.id}: {deleted.task}"
        updated, status = self.update_tool.execute(
            session,
            scheduler=self.scheduler,
            reminder=reminder,
            incoming_text=ai_run.message_text,
            timezone_name=pref.timezone,
            time_phrase=envelope.reminder.datetime_text or "",
            retry_interval_minutes=pref.wakeup_retry_interval_minutes,
            max_attempts=pref.wakeup_max_attempts,
            source_mode="resolution",
            interpretation_json=envelope.model_dump_json(),
            target_selector_json=envelope.target.model_dump_json(),
            ai_confidence=float(ai_run.confidence or 0),
        )
        return status if updated is None else f"Updated reminder #{updated.id}."

    def _get_pending_state(self, session, *, chat_id: int) -> PendingConversationState | None:
        stmt = select(ConversationState).where(ConversationState.chat_id == chat_id)
        row = session.scalar(stmt)
        if row is None or row.pending_intent != "phase6_follow_up":
            return None
        try:
            return PendingConversationState.model_validate_json(row.state_json)
        except Exception:
            return None

    def _save_pending_state(self, session, *, chat_id: int, telegram_user_id: int, state: PendingConversationState) -> None:
        stmt = select(ConversationState).where(ConversationState.chat_id == chat_id)
        row = session.scalar(stmt)
        payload = state.model_dump_json()
        if row is None:
            row = ConversationState(
                chat_id=chat_id,
                telegram_user_id=telegram_user_id,
                pending_intent="phase6_follow_up",
                state_json=payload,
            )
            session.add(row)
        else:
            row.telegram_user_id = telegram_user_id
            row.pending_intent = "phase6_follow_up"
            row.state_json = payload
        session.commit()

    def _clear_pending_state(self, session, *, chat_id: int) -> None:
        stmt = select(ConversationState).where(ConversationState.chat_id == chat_id)
        row = session.scalar(stmt)
        if row is not None and row.pending_intent == "phase6_follow_up":
            session.delete(row)
            session.commit()

    def _merge_pending_state(self, pending: PendingConversationState, message_text: str) -> InterpretationEnvelope:
        merged = InterpretationEnvelope(
            action=pending.action,
            confidence=0.78,
            reminder=pending.reminder.model_copy(deep=True),
            target=pending.target.model_copy(deep=True),
            preferences=pending.preferences.model_copy(deep=True),
            follow_up=FollowUp(needed=False, question=None, missing_fields=[]),
            user_message_summary=pending.user_message_summary,
            reasoning_tags=["follow_up_merge"],
            deadline_offsets=list(pending.deadline_offsets),
        )
        missing = list(pending.follow_up.missing_fields)
        text = message_text.strip()
        if missing:
            field = missing[0]
            if field in {"missing_task", "task"}:
                merged.reminder.task = text
            elif field in {"missing_datetime", "time_phrase", "deadline_phrase"}:
                merged.reminder.datetime_text = text
            elif field in {"missing_target", "target"}:
                merged.target.selector_text = text
            elif field in {"missing_preference_value", "preference_value"}:
                self._apply_preference_value(merged.preferences, text)
        return merged

    def _apply_preference_value(self, prefs, text: str) -> None:
        lowered = text.lower().strip()
        if lowered.isdigit():
            value = int(lowered)
            if prefs.snooze_minutes is None:
                prefs.snooze_minutes = value
            elif prefs.wake_retry_minutes is None:
                prefs.wake_retry_minutes = value
            else:
                prefs.wake_max_attempts = value
            return
        if lowered in {"on", "enable", "enabled", "true"}:
            if prefs.daily_agenda_enabled is None:
                prefs.daily_agenda_enabled = True
            else:
                prefs.missed_summary_enabled = True
            return
        if lowered in {"off", "disable", "disabled", "false"}:
            if prefs.daily_agenda_enabled is None:
                prefs.daily_agenda_enabled = False
            else:
                prefs.missed_summary_enabled = False
            return
        prefs.daily_agenda_time = text
