from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.ai.checker import InterpretationChecker
from app.ai.interpreter import InterpreterResult, StructuredInterpreter
from app.ai.schemas import ConfirmationState, FollowUp, InterpretationEnvelope, PendingConversationState, ReminderDraft, TargetSelector, PreferencePatch
from app.config import Settings
from app.learning import EvalBuilder, ExampleMemoryStore, FeedbackStore, RuleSuggester
from app.models import ConversationState
from app.parser import split_task_and_time_phrase
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
from app.ai.time_normalizer import looks_like_time_phrase, normalize_time_phrase
from app.semantic_judgment import (
    apply_semantic_judgment,
    build_semantic_confirmation_text,
    detect_repair_signal,
    infer_indirect_reminder,
    should_confirm_for_semantics,
)


@dataclass(slots=True)
class BotResponsePlan:
    text: str
    reply_markup: InlineKeyboardMarkup | None = None


def latest_to_draft(reminder) -> ReminderDraft:
    return ReminderDraft(
        task=reminder.task,
        datetime_text=None,
        recurrence_text=None,
        timezone=reminder.timezone,
        is_wake_up=bool(reminder.requires_ack),
        requires_ack=bool(reminder.requires_ack),
        priority="high" if reminder.requires_ack else "normal",
    )


def latest_to_target(reminder) -> TargetSelector:
    return TargetSelector(
        selector_text=f"reminder #{reminder.id}",
        reminder_id=reminder.id,
        task_hint=reminder.task,
    )


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
        self.feedback = FeedbackStore()
        self.example_memory = ExampleMemoryStore()
        self.eval_builder = EvalBuilder()
        self.rule_suggester = RuleSuggester()
        self.create_tool = CreateReminderTool(self.reminder_service)
        self.list_tool = ListRemindersTool(self.reminder_service)
        self.update_tool = UpdateReminderTool(self.reminder_service)
        self.delete_tool = DeleteReminderTool(self.reminder_service)
        self.today_tool = TodayAgendaTool(self.reminder_service)
        self.pref_tool = SetPreferencesTool(self.reminder_service)
        self.deadline_tool = DeadlineChainTool(self.reminder_service)
        self.missed_tool = MissedSummaryTool(self.reminder_service)

    def handle_user_message(self, session, *, chat_id: int, telegram_user_id: int, message_text: str) -> BotResponsePlan:
        cleaned = " ".join((message_text or "").strip().split())
        lowered = cleaned.lower()
        smalltalk = {
            "thanks": "You're welcome.",
            "thank you": "You're welcome.",
            "thx": "You're welcome.",
            "ok": "Okay.",
            "okay": "Okay.",
            "hello": "How can I assist you?",
            "hi": "How can I assist you?",
            "hey": "How can I assist you?",
        }
        if lowered in smalltalk:
            return BotResponsePlan(text=smalltalk[lowered])

        pref = self.reminder_service.get_or_create_preferences(
            session,
            chat_id=chat_id,
            telegram_user_id=telegram_user_id,
            timezone_name=self.settings.default_timezone,
        )
        open_reminders = self.reminder_service.list_open_reminders(session, chat_id=chat_id)
        pending_state = self._get_pending_state(session, chat_id=chat_id)
        confirmation_state = self._get_confirmation_state(session, chat_id=chat_id)

        if confirmation_state is not None:
            if lowered in {"yes", "y", "confirm", "confirm it"}:
                return self._execute_confirmation_state(
                    session,
                    confirmation_state=confirmation_state,
                    pref=pref,
                    chat_id=chat_id,
                    telegram_user_id=telegram_user_id,
                    open_reminders=open_reminders,
                )
            if lowered in {"no", "n", "cancel", "stop"}:
                self._clear_confirmation_state(session, chat_id=chat_id)
                return BotResponsePlan(text="Okay — I cancelled that pending action.")

        repair_plan = self._try_repair_conversation(
            session,
            chat_id=chat_id,
            telegram_user_id=telegram_user_id,
            message_text=message_text,
            open_reminders=open_reminders,
        )
        if repair_plan is not None:
            return repair_plan

        learned_examples = self.example_memory.format_for_prompt(
            self.example_memory.find_similar(session, telegram_user_id=telegram_user_id, message_text=message_text)
        )
        interpreter_result = self.interpreter.interpret(
            message_text=message_text,
            timezone_name=pref.timezone,
            pending_state=pending_state,
            open_reminders=open_reminders,
            preference_snapshot=self.reminder_service.format_preferences_summary(pref),
            learned_examples=learned_examples,
        )
        envelope = interpreter_result.envelope
        if pending_state is not None and envelope.action == "clarify":
            envelope = self._merge_pending_state(pending_state, message_text)
        if envelope.action == "clarify":
            inferred = infer_indirect_reminder(message_text)
            if inferred is not None:
                envelope = inferred
        envelope = apply_semantic_judgment(message_text, envelope, pref.timezone)
        checker_result = self.checker.check(envelope=envelope, open_reminders=open_reminders)

        ai_run = self.audit.record_ai_run(
            session,
            user_id=telegram_user_id,
            chat_id=chat_id,
            message_text=message_text,
            system_prompt_version="phase6_4_semantic_repair_v1",
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
            source_message = pending_state.source_message_text if pending_state and pending_state.source_message_text else message_text
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
                source_message_text=source_message,
                follow_up_turns=(pending_state.follow_up_turns + 1) if pending_state is not None else 1,
            )
            self._save_pending_state(session, chat_id=chat_id, telegram_user_id=telegram_user_id, state=pending)
            self._clear_confirmation_state(session, chat_id=chat_id)
            self.audit.record_action(
                session,
                user_id=telegram_user_id,
                reminder_id=None,
                action_name=envelope.action,
                action_args_json=pending.model_dump_json(),
                executor_result_json=json.dumps({"follow_up": follow_up_text}),
                status="follow_up",
            )
            self.feedback.record(
                session,
                chat_id=chat_id,
                telegram_user_id=telegram_user_id,
                message_text=message_text,
                phase="follow_up",
                outcome="needs_more_info",
                error_code=checker_result.issues[0] if checker_result.issues else None,
                details={"action": envelope.action, "question": follow_up_text},
            )
            return BotResponsePlan(text=follow_up_text)

        if self._should_request_confirmation(
            envelope=envelope,
            interpreter_result=interpreter_result,
            confidence=checker_result.confidence,
        ):
            self._clear_pending_state(session, chat_id=chat_id)
            state = ConfirmationState(
                action=envelope.action,
                envelope=envelope,
                ai_run_id=ai_run.id,
                confidence=checker_result.confidence,
                model_name=interpreter_result.model_name,
                source_message_text=pending_state.source_message_text if pending_state and pending_state.source_message_text else message_text,
                confirmation_reason=self._confirmation_reason(envelope, checker_result.confidence),
            )
            self._save_confirmation_state(session, chat_id=chat_id, telegram_user_id=telegram_user_id, state=state)
            self.feedback.record(
                session,
                chat_id=chat_id,
                telegram_user_id=telegram_user_id,
                message_text=message_text,
                phase="confirmation",
                outcome="requested",
                details={"action": envelope.action, "confidence": checker_result.confidence},
            )
            return BotResponsePlan(
                text=self._build_confirmation_text(state),
                reply_markup=self._build_confirmation_keyboard(),
            )

        self._clear_pending_state(session, chat_id=chat_id)
        self._clear_confirmation_state(session, chat_id=chat_id)
        return self._execute_action(
            session,
            envelope=envelope,
            interpreter_result=interpreter_result,
            confidence=checker_result.confidence,
            pref=pref,
            chat_id=chat_id,
            telegram_user_id=telegram_user_id,
            open_reminders=open_reminders,
            source_text=pending_state.source_message_text if pending_state and pending_state.source_message_text else message_text,
            original_message_text=message_text,
            ai_run_id=ai_run.id,
        )

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
            if deleted is not None:
                self._record_success_learning(session, chat_id=chat_id, telegram_user_id=telegram_user_id, source_text=ai_run.message_text, action_name=envelope.action, task=deleted.task, time_phrase=None, learned_from_follow_up=False)
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
        if updated is not None:
            self._record_success_learning(session, chat_id=chat_id, telegram_user_id=telegram_user_id, source_text=ai_run.message_text, action_name=envelope.action, task=updated.task, time_phrase=envelope.reminder.datetime_text, learned_from_follow_up=False)
        return status if updated is None else f"Updated reminder #{updated.id}."

    def handle_confirmation_choice(self, session, *, choice: str, chat_id: int, telegram_user_id: int) -> str:
        pref = self.reminder_service.get_or_create_preferences(
            session,
            chat_id=chat_id,
            telegram_user_id=telegram_user_id,
            timezone_name=self.settings.default_timezone,
        )
        confirmation_state = self._get_confirmation_state(session, chat_id=chat_id)
        if confirmation_state is None:
            return "That confirmation has expired. Please send the request again."
        if choice == "cancel":
            self._clear_confirmation_state(session, chat_id=chat_id)
            return "Okay — I cancelled that pending action."
        if choice == "edit":
            self._clear_confirmation_state(session, chat_id=chat_id)
            return "Okay — send me the corrected reminder in one message."
        if choice != "confirm":
            return "That confirmation action is not supported."

        open_reminders = self.reminder_service.list_open_reminders(session, chat_id=chat_id)
        plan = self._execute_confirmation_state(
            session,
            confirmation_state=confirmation_state,
            pref=pref,
            chat_id=chat_id,
            telegram_user_id=telegram_user_id,
            open_reminders=open_reminders,
        )
        return plan.text

    def _execute_confirmation_state(self, session, *, confirmation_state: ConfirmationState, pref, chat_id: int, telegram_user_id: int, open_reminders: list) -> BotResponsePlan:
        self._clear_confirmation_state(session, chat_id=chat_id)
        interpreter_result = InterpreterResult(
            envelope=confirmation_state.envelope,
            raw_response_text=None,
            model_name=confirmation_state.model_name or "confirmed",
            validation_ok=True,
            error_message=None,
        )
        return self._execute_action(
            session,
            envelope=confirmation_state.envelope,
            interpreter_result=interpreter_result,
            confidence=confirmation_state.confidence,
            pref=pref,
            chat_id=chat_id,
            telegram_user_id=telegram_user_id,
            open_reminders=open_reminders,
            source_text=confirmation_state.source_message_text or "",
            original_message_text=confirmation_state.source_message_text or "",
            was_confirmed=True,
            ai_run_id=confirmation_state.ai_run_id,
        )

    def _execute_action(
        self,
        session,
        *,
        envelope: InterpretationEnvelope,
        interpreter_result: InterpreterResult,
        confidence: float,
        pref,
        chat_id: int,
        telegram_user_id: int,
        open_reminders: list,
        source_text: str,
        original_message_text: str,
        was_confirmed: bool = False,
        ai_run_id: int | None = None,
    ) -> BotResponsePlan:
        if envelope.action == "help":
            return BotResponsePlan(
                text=(
                    "Try things like:\n"
                    "• Remind me tomorrow at 7 PM to pay rent\n"
                    "• Remind me to go for Sony headset repair today morning 9am\n"
                    "• Wake me up today morning 8\n"
                    "• Show my reminders\n"
                    "• Move my wake-up to 7 AM\n"
                    "• What do I have today\n"
                    "• Set my snooze to 10 minutes"
                )
            )

        if envelope.action == "list_reminders":
            self.feedback.record(session, chat_id=chat_id, telegram_user_id=telegram_user_id, message_text=original_message_text, phase="assistant", outcome="list")
            return BotResponsePlan(text=self.list_tool.execute(session, chat_id=chat_id))

        if envelope.action == "today_agenda":
            self.feedback.record(session, chat_id=chat_id, telegram_user_id=telegram_user_id, message_text=original_message_text, phase="assistant", outcome="today_agenda")
            return BotResponsePlan(text=self.today_tool.execute(session, chat_id=chat_id, timezone_name=pref.timezone))

        if envelope.action == "missed_summary":
            self.feedback.record(session, chat_id=chat_id, telegram_user_id=telegram_user_id, message_text=original_message_text, phase="assistant", outcome="missed_summary")
            return BotResponsePlan(text=self.missed_tool.execute(session, chat_id=chat_id))

        if envelope.action == "set_preferences":
            text = self.pref_tool.execute(
                session,
                scheduler=self.scheduler,
                preference=pref,
                preferences_patch=envelope.preferences,
                timezone_name=pref.timezone,
            )
            self.audit.record_action(session, user_id=telegram_user_id, reminder_id=None, action_name=envelope.action, action_args_json=envelope.model_dump_json(), executor_result_json=json.dumps({"text": text}), status="success")
            self.feedback.record(session, chat_id=chat_id, telegram_user_id=telegram_user_id, message_text=original_message_text, phase="preferences", outcome="success")
            return BotResponsePlan(text=text)

        if envelope.action == "deadline_chain":
            reminders, text = self.deadline_tool.execute(
                session,
                scheduler=self.scheduler,
                incoming_text=source_text or original_message_text,
                telegram_user_id=telegram_user_id,
                chat_id=chat_id,
                timezone_name=pref.timezone,
                task=envelope.reminder.task or "deadline",
                deadline_phrase=envelope.reminder.datetime_text or "",
                offsets=envelope.deadline_offsets,
            )
            self.audit.record_action(session, user_id=telegram_user_id, reminder_id=reminders[0].id if reminders else None, action_name=envelope.action, action_args_json=envelope.model_dump_json(), executor_result_json=json.dumps({"text": text}), status="success" if reminders else "error")
            if reminders:
                self.feedback.record(session, chat_id=chat_id, telegram_user_id=telegram_user_id, message_text=original_message_text, phase="deadline_chain", outcome="confirmed_success" if was_confirmed else "success")
            else:
                self._record_failure_learning(session, chat_id=chat_id, telegram_user_id=telegram_user_id, message_text=original_message_text, action_name=envelope.action, details={"text": text})
            return BotResponsePlan(text=text)

        if envelope.action == "create_reminder":
            duplicates = self.duplicates.find_possible_duplicates(
                reminders=open_reminders,
                task=envelope.reminder.task or "",
                due_repr=envelope.reminder.datetime_text or "",
                recurrence=envelope.reminder.recurrence_text,
            )
            if duplicates:
                self.feedback.record(session, chat_id=chat_id, telegram_user_id=telegram_user_id, message_text=original_message_text, phase="create", outcome="duplicate_block")
                return BotResponsePlan(text="That looks similar to an existing reminder. Say it again with a clearer time, or use /list to check first.")
            reminder, status = self.create_tool.execute(
                session,
                scheduler=self.scheduler,
                incoming_text=source_text or original_message_text,
                telegram_user_id=telegram_user_id,
                chat_id=chat_id,
                timezone_name=pref.timezone,
                task=envelope.reminder.task or "",
                time_phrase=envelope.reminder.datetime_text or "",
                requires_ack=bool(envelope.reminder.requires_ack),
                retry_interval_minutes=pref.wakeup_retry_interval_minutes,
                max_attempts=pref.wakeup_max_attempts,
                source_mode="llm-confirmed" if was_confirmed and interpreter_result.model_name != "rule-fallback" else ("llm" if interpreter_result.model_name != "rule-fallback" else "rule"),
                interpretation_json=envelope.model_dump_json(),
                target_selector_json=envelope.target.model_dump_json(),
                ai_confidence=confidence,
            )
            if reminder is None:
                self.audit.record_action(session, user_id=telegram_user_id, reminder_id=None, action_name=envelope.action, action_args_json=envelope.model_dump_json(), executor_result_json=json.dumps({"status": status}), status="error")
                self._record_failure_learning(session, chat_id=chat_id, telegram_user_id=telegram_user_id, message_text=original_message_text, action_name=envelope.action, details={"status": status, "time_phrase": envelope.reminder.datetime_text})
                return BotResponsePlan(text=status)
            text = f"Okay — I created reminder #{reminder.id}: {reminder.task}."
            if reminder.requires_ack:
                text += " This wake-up reminder will repeat until you acknowledge it."
            elif was_confirmed:
                text = "Confirmed — " + text[0].lower() + text[1:]
            self.audit.record_action(session, user_id=telegram_user_id, reminder_id=reminder.id, action_name=envelope.action, action_args_json=envelope.model_dump_json(), executor_result_json=json.dumps({"text": text}), status="success")
            self._record_success_learning(session, chat_id=chat_id, telegram_user_id=telegram_user_id, source_text=source_text or original_message_text, action_name=envelope.action, task=reminder.task, time_phrase=envelope.reminder.datetime_text, learned_from_follow_up=False)
            return BotResponsePlan(text=text)

        if envelope.action in {"update_reminder", "delete_reminder"}:
            resolution = self.target_resolution.resolve(
                session=session,
                ai_run_id=ai_run_id or 0,
                action_name=envelope.action,
                selector_text=envelope.target.selector_text,
                reminder_id=envelope.target.reminder_id,
                reminders=open_reminders,
            )
            if resolution.status == "none":
                self._record_failure_learning(session, chat_id=chat_id, telegram_user_id=telegram_user_id, message_text=original_message_text, action_name=envelope.action, details={"status": "no_target"})
                return BotResponsePlan(text=resolution.message or "I couldn't find that reminder.")
            if resolution.status == "ambiguous":
                if not self.settings.ai_enable_resolution_buttons or not resolution.candidates or not ai_run_id:
                    return BotResponsePlan(text=resolution.message or "That matches more than one reminder.")
                keyboard = self.target_resolution.build_keyboard(ai_run_id=ai_run_id, candidates=resolution.candidates)
                return BotResponsePlan(text=resolution.message or "Please choose a reminder.", reply_markup=keyboard)
            target = resolution.selected
            assert target is not None
            if envelope.action == "delete_reminder":
                deleted, status = self.delete_tool.execute(session, scheduler=self.scheduler, chat_id=chat_id, reminder=target)
                if deleted is None:
                    self._record_failure_learning(session, chat_id=chat_id, telegram_user_id=telegram_user_id, message_text=original_message_text, action_name=envelope.action, details={"status": status})
                    return BotResponsePlan(text=status)
                text = f"Cancelled reminder #{deleted.id}: {deleted.task}"
                self.audit.record_action(session, user_id=telegram_user_id, reminder_id=deleted.id, action_name=envelope.action, action_args_json=envelope.model_dump_json(), executor_result_json=json.dumps({"text": text}), status="success")
                self._record_success_learning(session, chat_id=chat_id, telegram_user_id=telegram_user_id, source_text=original_message_text, action_name=envelope.action, task=deleted.task, time_phrase=None, learned_from_follow_up=False)
                return BotResponsePlan(text=text)
            updated, status = self.update_tool.execute(
                session,
                scheduler=self.scheduler,
                reminder=target,
                incoming_text=source_text or original_message_text,
                timezone_name=pref.timezone,
                time_phrase=envelope.reminder.datetime_text or "",
                retry_interval_minutes=pref.wakeup_retry_interval_minutes,
                max_attempts=pref.wakeup_max_attempts,
                source_mode="llm-confirmed" if was_confirmed and interpreter_result.model_name != "rule-fallback" else ("llm" if interpreter_result.model_name != "rule-fallback" else "rule"),
                interpretation_json=envelope.model_dump_json(),
                target_selector_json=envelope.target.model_dump_json(),
                ai_confidence=confidence,
            )
            if updated is None:
                self._record_failure_learning(session, chat_id=chat_id, telegram_user_id=telegram_user_id, message_text=original_message_text, action_name=envelope.action, details={"status": status, "time_phrase": envelope.reminder.datetime_text})
                return BotResponsePlan(text=status)
            text = f"Updated reminder #{updated.id}."
            self.audit.record_action(session, user_id=telegram_user_id, reminder_id=updated.id, action_name=envelope.action, action_args_json=envelope.model_dump_json(), executor_result_json=json.dumps({"text": text}), status="success")
            self._record_success_learning(session, chat_id=chat_id, telegram_user_id=telegram_user_id, source_text=original_message_text, action_name=envelope.action, task=updated.task, time_phrase=envelope.reminder.datetime_text, learned_from_follow_up=False)
            return BotResponsePlan(text=text)

        self._record_failure_learning(session, chat_id=chat_id, telegram_user_id=telegram_user_id, message_text=original_message_text, action_name="clarify", details={"reason": "unknown_action"})
        return BotResponsePlan(text="I couldn't understand that. Try /help.")

    def _should_request_confirmation(self, *, envelope: InterpretationEnvelope, interpreter_result: InterpreterResult, confidence: float) -> bool:
        if envelope.action not in {"create_reminder", "update_reminder", "delete_reminder", "deadline_chain"}:
            return False
        if should_confirm_for_semantics(envelope):
            return True
        if interpreter_result.model_name == "rule-fallback" and not envelope.reminder.is_wake_up:
            return False
        if envelope.reminder.is_wake_up and self.settings.ai_confirm_wakeups:
            return True
        if confidence >= self.settings.ai_min_auto_execute_confidence:
            return False
        return confidence >= self.settings.ai_confirmation_min_confidence

    def _confirmation_reason(self, envelope: InterpretationEnvelope, confidence: float) -> str:
        tags = set(envelope.reasoning_tags)
        if "repair_conversation" in tags:
            return "repair_conversation"
        if "suspicious_time" in tags:
            return "suspicious_time"
        if "approximate_time" in tags:
            return "approximate_time"
        if "indirect_intent" in tags:
            return "indirect_intent"
        if envelope.reminder.is_wake_up and self.settings.ai_confirm_wakeups:
            return "wake_up_safety"
        if confidence < self.settings.ai_min_auto_execute_confidence:
            return "medium_confidence"
        return "user_confirmation"

    def _build_confirmation_text(self, state: ConfirmationState) -> str:
        envelope = state.envelope
        semantic_text = build_semantic_confirmation_text(envelope)
        if semantic_text:
            return semantic_text
        task = envelope.reminder.task or "this reminder"
        when = envelope.reminder.datetime_text or "that time"
        if envelope.action == "create_reminder":
            if envelope.reminder.requires_ack or envelope.reminder.is_wake_up:
                return f"I understood this as a wake-up reminder for {when}. Confirm before I schedule it?"
            return f"I understood this as: remind you to {task} at {when}. Confirm before I schedule it?"
        if envelope.action == "update_reminder":
            target = envelope.target.selector_text or (f"reminder #{envelope.target.reminder_id}" if envelope.target.reminder_id else "that reminder")
            return f"I understood this as: update {target} to {when}. Confirm?"
        if envelope.action == "delete_reminder":
            target = envelope.target.selector_text or (f"reminder #{envelope.target.reminder_id}" if envelope.target.reminder_id else "that reminder")
            return f"I understood this as: cancel {target}. Confirm?"
        if envelope.action == "deadline_chain":
            return f"I understood this as: create deadline reminders for {task}. Confirm?"
        return "Please confirm this action."

    def _build_confirmation_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("✅ Confirm", callback_data="confirm:confirm")],
                [InlineKeyboardButton("✏️ Edit", callback_data="confirm:edit"), InlineKeyboardButton("❌ Cancel", callback_data="confirm:cancel")],
            ]
        )

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
            row = ConversationState(chat_id=chat_id, telegram_user_id=telegram_user_id, pending_intent="phase6_follow_up", state_json=payload)
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

    def _get_confirmation_state(self, session, *, chat_id: int) -> ConfirmationState | None:
        stmt = select(ConversationState).where(ConversationState.chat_id == chat_id)
        row = session.scalar(stmt)
        if row is None or row.pending_intent != "phase6_confirm":
            return None
        try:
            return ConfirmationState.model_validate_json(row.state_json)
        except Exception:
            return None

    def _save_confirmation_state(self, session, *, chat_id: int, telegram_user_id: int, state: ConfirmationState) -> None:
        stmt = select(ConversationState).where(ConversationState.chat_id == chat_id)
        row = session.scalar(stmt)
        payload = state.model_dump_json()
        if row is None:
            row = ConversationState(chat_id=chat_id, telegram_user_id=telegram_user_id, pending_intent="phase6_confirm", state_json=payload)
            session.add(row)
        else:
            row.telegram_user_id = telegram_user_id
            row.pending_intent = "phase6_confirm"
            row.state_json = payload
        session.commit()

    def _clear_confirmation_state(self, session, *, chat_id: int) -> None:
        stmt = select(ConversationState).where(ConversationState.chat_id == chat_id)
        row = session.scalar(stmt)
        if row is not None and row.pending_intent == "phase6_confirm":
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
            parsed_task, parsed_time = split_task_and_time_phrase(text)
            if field in {"missing_task", "task"}:
                if parsed_task:
                    merged.reminder.task = parsed_task
                elif not looks_like_time_phrase(text):
                    merged.reminder.task = text
                if merged.reminder.datetime_text is None and parsed_time:
                    merged.reminder.datetime_text = normalize_time_phrase(parsed_time)
            elif field in {"missing_datetime", "time_phrase", "deadline_phrase"}:
                if parsed_time:
                    merged.reminder.datetime_text = normalize_time_phrase(parsed_time)
                else:
                    merged.reminder.datetime_text = normalize_time_phrase(text)
            elif field in {"missing_target", "target"}:
                merged.target.selector_text = text
            elif field in {"missing_preference_value", "preference_value"}:
                self._apply_preference_value(merged.preferences, text)
        return merged

    def _try_repair_conversation(self, session, *, chat_id: int, telegram_user_id: int, message_text: str, open_reminders: list) -> BotResponsePlan | None:
        signal = detect_repair_signal(message_text)
        if signal is None:
            return None
        if not open_reminders:
            if signal.needs_follow_up and signal.ask_user:
                return BotResponsePlan(text=signal.ask_user)
            return None

        latest = max(open_reminders, key=lambda reminder: reminder.id)
        if signal.needs_follow_up and signal.ask_user:
            pending = PendingConversationState(
                action="update_reminder",
                reminder=latest_to_draft(latest),
                target=latest_to_target(latest),
                follow_up=FollowUp(
                    needed=True,
                    question=f"Understood — what time should I use for reminder #{latest.id} ({latest.task}) instead?",
                    missing_fields=["time_phrase"],
                ),
                user_message_summary="repair_follow_up",
                source_message_text=message_text,
                follow_up_turns=1,
            )
            self._save_pending_state(session, chat_id=chat_id, telegram_user_id=telegram_user_id, state=pending)
            return BotResponsePlan(text=pending.follow_up.question or signal.ask_user)

        if signal.corrected_time_phrase:
            envelope = InterpretationEnvelope(
                action="update_reminder",
                confidence=0.90,
                reminder=latest_to_draft(latest),
                target=latest_to_target(latest),
                preferences=PreferencePatch(),
                follow_up=FollowUp(needed=False, question=None, missing_fields=[]),
                user_message_summary="repair_conversation",
                reasoning_tags=["repair_conversation"],
                deadline_offsets=[],
            )
            envelope.reminder.datetime_text = signal.corrected_time_phrase
            envelope = apply_semantic_judgment(message_text, envelope, latest.timezone)
            state = ConfirmationState(
                action=envelope.action,
                envelope=envelope,
                ai_run_id=None,
                confidence=0.90,
                model_name="semantic-repair",
                source_message_text=message_text,
                confirmation_reason="repair_conversation",
            )
            self._save_confirmation_state(session, chat_id=chat_id, telegram_user_id=telegram_user_id, state=state)
            return BotResponsePlan(
                text=self._build_confirmation_text(state),
                reply_markup=self._build_confirmation_keyboard(),
            )

        return None

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

    def _record_success_learning(self, session, *, chat_id: int, telegram_user_id: int, source_text: str, action_name: str, task: str | None, time_phrase: str | None, learned_from_follow_up: bool) -> None:
        self.feedback.record(session, chat_id=chat_id, telegram_user_id=telegram_user_id, message_text=source_text, phase=action_name, outcome="success", details={"task": task, "time_phrase": time_phrase})
        self.example_memory.remember(session, chat_id=chat_id, telegram_user_id=telegram_user_id, source_text=source_text, action_name=action_name, resolved_task=task, resolved_time_phrase=time_phrase, learned_from_follow_up=learned_from_follow_up)
        if time_phrase:
            self.rule_suggester.remember_time_phrase(session, raw_phrase=time_phrase)

    def _record_failure_learning(self, session, *, chat_id: int, telegram_user_id: int, message_text: str, action_name: str, details: dict) -> None:
        self.feedback.record(session, chat_id=chat_id, telegram_user_id=telegram_user_id, message_text=message_text, phase=action_name, outcome="failure", error_code="execution_failure", details=details)
        if self.settings.ai_enable_eval_logging:
            self.eval_builder.add_candidate(session, label=f"auto::{action_name}", input_text=message_text, expected_action="create_reminder" if action_name == "create_reminder" else "clarify", expected_json=details)
