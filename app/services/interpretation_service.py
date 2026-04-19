from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.ai.checker import InterpretationChecker
from app.ai.interpreter import InterpreterResult, StructuredInterpreter
from app.ai.schemas import ConfirmationState, FollowUp, InterpretationEnvelope, PendingConversationState, ReminderDraft, TargetSelector, PreferencePatch
from app.config import Settings
from app.learning import EvalBuilder, ExampleMemoryStore, FeedbackStore, RuleSuggester, SelfLearningEngine
from app.models import ConversationState
from app.parser import split_task_and_time_phrase
from app.repositories.ai_run_repo import AiRunRepository
from app.assistant_features import local_day_bounds_utc
from app.service import ReminderService, reminder_summary_line
from app.services.audit_service import AuditService
from app.services.duplicate_detection_service import DuplicateDetectionService
from app.services.target_resolution_service import TargetResolutionService
from app.phase7 import EvaluatorAgent, SemanticConflictDetector
from app.phase8 import MemoryProfileStore, MemoryReasoner
from app.phase9 import MultiPlanConfirmationState, MultiPlanItem, MultiReminderPlanner, ProactiveSuggester
from app.phase9_1 import GeneralResponder, LLMConversationRouter, ThreadConversationState, ThreadMemoryStore
from app.phase9_2 import ToolFirstRouter, ReferenceResolver, ReferenceContext, ReferenceMemoryStore, ChatReferenceState
from app.phase9_3 import ConversationRepairAndClarifier
from app.phase10_1 import CalendarScreenshotImporter, CalendarImportError
from app.tools.create_reminder import CreateReminderTool
from app.tools.deadline_chain import DeadlineChainTool
from app.tools.delete_reminder import DeleteReminderTool
from app.tools.list_reminders import ListRemindersTool
from app.tools.missed_summary import MissedSummaryTool
from app.tools.set_preferences import SetPreferencesTool
from app.tools.today_agenda import TodayAgendaTool
from app.tools.update_reminder import UpdateReminderTool
from app.ai.time_normalizer import looks_like_time_phrase, normalize_time_phrase
from app.recurrence import format_dt_for_user
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
        self.self_learning = SelfLearningEngine()
        self.create_tool = CreateReminderTool(self.reminder_service)
        self.list_tool = ListRemindersTool(self.reminder_service)
        self.update_tool = UpdateReminderTool(self.reminder_service)
        self.delete_tool = DeleteReminderTool(self.reminder_service)
        self.today_tool = TodayAgendaTool(self.reminder_service)
        self.pref_tool = SetPreferencesTool(self.reminder_service)
        self.deadline_tool = DeadlineChainTool(self.reminder_service)
        self.missed_tool = MissedSummaryTool(self.reminder_service)
        self.conflict_detector = SemanticConflictDetector()
        self.evaluator = EvaluatorAgent(self.conflict_detector)
        self.memory_profiles = MemoryProfileStore()
        self.memory_reasoner = MemoryReasoner()
        self.multi_planner = MultiReminderPlanner()
        self.proactive_suggester = ProactiveSuggester()
        self.thread_memory = ThreadMemoryStore()
        self.conversation_router = LLMConversationRouter(settings)
        self.general_responder = GeneralResponder(settings)
        self.tool_router = ToolFirstRouter()
        self.reference_resolver = ReferenceResolver()
        self.reference_memory = ReferenceMemoryStore()
        self.repair_clarifier = ConversationRepairAndClarifier()
        self.calendar_importer = CalendarScreenshotImporter(
            default_timezone=settings.default_timezone,
            lead_minutes=settings.calendar_import_lead_minutes,
            fallback_to_today=settings.calendar_import_fallback_to_today,
            groq_api_key=settings.groq_api_key,
            groq_model=settings.groq_model,
            use_vision_llm=settings.calendar_import_use_vision_llm,
        )

    def _looks_like_new_request(self, message_text: str) -> bool:
        lowered = ' '.join((message_text or '').strip().lower().split())
        prefixes = (
            'remind me',
            'wake me up',
            'wake up me',
            'wake up',
            'move ',
            'change ',
            'reschedule ',
            'update ',
            'delete ',
            'cancel ',
            'remove ',
            '/list',
            '/today',
            '/prefs',
            '/delete',
            'show my reminders',
            'list my reminders',
            'what are my reminders',
            'what do i have today',
            "what's on today",
            'today agenda',
            'what did i miss',
            'send my daily agenda',
            'set my ',
        )
        if lowered.startswith(prefixes):
            return True
        time_tokens = ('today', 'tomorrow', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday', 'morning', 'afternoon', 'evening', 'night', 'am', 'pm')
        action_tokens = ('remind', 'wake', 'schedule', 'list', 'update', 'delete', 'cancel', 'remove')
        return any(tok in lowered for tok in time_tokens) and any(tok in lowered for tok in action_tokens)

    def _extract_wake_time_phrase(self, message_text: str) -> str | None:
        normalized = ' '.join((message_text or '').strip().split())
        match = re.match(r'^\s*(?:wake me up|wake up me|wake up)\b\s*(.*)$', normalized, re.IGNORECASE)
        if not match:
            return None
        remainder = (match.group(1) or '').strip(' .')
        if remainder.lower().startswith('at '):
            remainder = remainder[3:].strip()
        return normalize_time_phrase(remainder) if remainder else None

    def _build_wake_up_envelope(self, message_text: str) -> InterpretationEnvelope | None:
        if self._extract_wake_time_phrase(message_text) is None and not re.match(r'^\s*(?:wake me up|wake up me|wake up)\b', message_text or '', re.IGNORECASE):
            return None
        time_phrase = self._extract_wake_time_phrase(message_text)
        follow_up = FollowUp(needed=time_phrase is None, question='What time should I wake you up?' if time_phrase is None else None, missing_fields=['time_phrase'] if time_phrase is None else [])
        return InterpretationEnvelope(
            action='create_reminder',
            confidence=0.9 if time_phrase else 0.82,
            reminder=ReminderDraft(
                task='wake up',
                datetime_text=time_phrase,
                recurrence_text=time_phrase,
                timezone=None,
                is_wake_up=True,
                requires_ack=True,
                priority='high',
            ),
            target=TargetSelector(),
            preferences=PreferencePatch(),
            follow_up=follow_up,
            user_message_summary='wake_up_fastpath',
            reasoning_tags=['wake_up_fastpath'],
            deadline_offsets=[],
        )

    def handle_user_message(self, session, *, chat_id: int, telegram_user_id: int, message_text: str) -> BotResponsePlan:
        cleaned = " ".join((message_text or "").strip().split())
        lowered = cleaned.lower()

        plan_state = self._get_multi_plan_state(session, chat_id=chat_id)
        pending_state = self._get_pending_state(session, chat_id=chat_id)
        confirmation_state = self._get_confirmation_state(session, chat_id=chat_id)
        thread_state = self._get_thread_state(session, chat_id=chat_id)

        active_thread = thread_state is not None and thread_state.status != 'idle'
        route = self.conversation_router.route(
            message_text=cleaned,
            has_active_thread=any([plan_state is not None, pending_state is not None, confirmation_state is not None, active_thread]),
            has_pending_confirmation=plan_state is not None or confirmation_state is not None,
            has_pending_follow_up=pending_state is not None,
        )

        if plan_state is not None:
            if lowered in {"yes", "y", "confirm", "confirm it", "ok", "okay"} or route.route == 'confirmation_reply':
                self._clear_thread_state(session, chat_id=chat_id)
                return self._execute_multi_plan_state(
                    session,
                    state=plan_state,
                    chat_id=chat_id,
                    telegram_user_id=telegram_user_id,
                )
            if lowered in {"no", "n", "cancel", "stop"}:
                self._clear_multi_plan_state(session, chat_id=chat_id)
                self._clear_thread_state(session, chat_id=chat_id)
                return BotResponsePlan(text="Okay — I cancelled that multi-reminder plan.")
            if self._looks_like_new_request(cleaned):
                self._clear_multi_plan_state(session, chat_id=chat_id)
                plan_state = None

        pref = self.reminder_service.get_or_create_preferences(
            session,
            chat_id=chat_id,
            telegram_user_id=telegram_user_id,
            timezone_name=self.settings.default_timezone,
        )
        open_reminders = self.reminder_service.list_open_reminders(session, chat_id=chat_id)
        reference_state = self.reference_memory.get(session, chat_id=chat_id)
        ref_ctx = ReferenceContext(
            last_discussed_task=reference_state.last_discussed_task,
            last_discussed_time_phrase=reference_state.last_discussed_time_phrase,
            last_created_reminder_id=reference_state.last_created_reminder_id,
            last_listed_reminder_ids=reference_state.last_listed_reminder_ids,
            last_referenced_reminder_id=reference_state.last_referenced_reminder_id,
        )

        current_task, current_time_phrase = self._conversation_task_time(
            pending_state=pending_state,
            confirmation_state=confirmation_state,
            reference_state=reference_state,
            open_reminders=open_reminders,
        )
        repair_rewrite = self.repair_clarifier.maybe_rewrite(
            cleaned,
            current_task=current_task,
            current_time_phrase=current_time_phrase,
        )
        if repair_rewrite is not None:
            if repair_rewrite.message_text == '__CHANGE_TIME_ONLY__':
                if current_task:
                    pending = PendingConversationState(
                        action='create_reminder' if confirmation_state is None else confirmation_state.action,
                        reminder=(confirmation_state.envelope.reminder.model_copy(deep=True) if confirmation_state is not None else (pending_state.reminder.model_copy(deep=True) if pending_state is not None else ReminderDraft(task=current_task))),
                        target=(confirmation_state.envelope.target.model_copy(deep=True) if confirmation_state is not None else (pending_state.target.model_copy(deep=True) if pending_state is not None else TargetSelector())),
                        preferences=PreferencePatch(),
                        follow_up=FollowUp(needed=True, question=f"What time should I use? I'll keep the task as \"{current_task}\".", missing_fields=['time_phrase']),
                        user_message_summary='phase9_3_change_time_only',
                        source_message_text=confirmation_state.source_message_text if confirmation_state is not None else message_text,
                        follow_up_turns=(pending_state.follow_up_turns + 1) if pending_state is not None else 1,
                    )
                    self._save_pending_state(session, chat_id=chat_id, telegram_user_id=telegram_user_id, state=pending)
                    self._clear_confirmation_state(session, chat_id=chat_id)
                    return BotResponsePlan(text=pending.follow_up.question or 'What time should I use?')
            elif repair_rewrite.message_text == '__CHANGE_DATE_ONLY__':
                if current_task:
                    pending = PendingConversationState(
                        action='create_reminder' if confirmation_state is None else confirmation_state.action,
                        reminder=(confirmation_state.envelope.reminder.model_copy(deep=True) if confirmation_state is not None else (pending_state.reminder.model_copy(deep=True) if pending_state is not None else ReminderDraft(task=current_task))),
                        target=(confirmation_state.envelope.target.model_copy(deep=True) if confirmation_state is not None else (pending_state.target.model_copy(deep=True) if pending_state is not None else TargetSelector())),
                        preferences=PreferencePatch(),
                        follow_up=FollowUp(needed=True, question=f"What new date should I use? I'll keep the task as \"{current_task}\".", missing_fields=['time_phrase']),
                        user_message_summary='phase9_3_change_date_only',
                        source_message_text=confirmation_state.source_message_text if confirmation_state is not None else message_text,
                        follow_up_turns=(pending_state.follow_up_turns + 1) if pending_state is not None else 1,
                    )
                    self._save_pending_state(session, chat_id=chat_id, telegram_user_id=telegram_user_id, state=pending)
                    self._clear_confirmation_state(session, chat_id=chat_id)
                    return BotResponsePlan(text=pending.follow_up.question or 'What new date should I use?')
            else:
                cleaned = repair_rewrite.message_text
                lowered = cleaned.lower()

        tool_plan, cleaned = self._handle_tool_first_route(
            session,
            chat_id=chat_id,
            telegram_user_id=telegram_user_id,
            message_text=cleaned,
            lowered=lowered,
            pref=pref,
            open_reminders=open_reminders,
            reference_state=reference_state,
        )
        if tool_plan is not None:
            return tool_plan
        lowered = cleaned.lower()
        cleaned = self.reference_resolver.substitute_pronoun_create(cleaned, ref_ctx)
        lowered = cleaned.lower()
        pre_general_indirect = infer_indirect_reminder(cleaned)
        if pre_general_indirect is not None and pre_general_indirect.reminder.task:
            self.reference_memory.remember(
                session,
                chat_id=chat_id,
                telegram_user_id=telegram_user_id,
                task=pre_general_indirect.reminder.task,
                time_phrase=pre_general_indirect.reminder.datetime_text,
            )

        if route.route == 'general_chat' and plan_state is None and pending_state is None and confirmation_state is None and not self._looks_like_new_request(cleaned) and pre_general_indirect is None:
            self._clear_thread_state(session, chat_id=chat_id)
            return BotResponsePlan(text=self.general_responder.respond(message_text=cleaned))

        if pending_state is not None and self._looks_like_new_request(cleaned):
            self._clear_pending_state(session, chat_id=chat_id)
            pending_state = None

        if confirmation_state is not None and self._looks_like_new_request(cleaned):
            self._clear_confirmation_state(session, chat_id=chat_id)
            confirmation_state = None

        if route.route in {'reminder_conversation', 'repair_conversation'} and plan_state is None and pending_state is None and confirmation_state is None:
            self._save_thread_state(
                session,
                chat_id=chat_id,
                telegram_user_id=telegram_user_id,
                state=ThreadConversationState(
                    mode=route.route,
                    status='active',
                    turns=(thread_state.turns + 1) if thread_state is not None else 1,
                    draft_action=None,
                    draft_summary=None,
                    last_bot_prompt=None,
                    last_user_message=message_text,
                ),
            )

        multi_plan = self.multi_planner.detect(prepared_message_text if "prepared_message_text" in locals() else cleaned, timezone_name=pref.timezone)
        if multi_plan is not None:
            self._clear_pending_state(session, chat_id=chat_id)
            self._clear_confirmation_state(session, chat_id=chat_id)
            state = MultiPlanConfirmationState(
                source_message_text=message_text,
                items=multi_plan.items,
                confidence=multi_plan.confidence,
                shared_context=multi_plan.shared_context,
            )
            self._save_multi_plan_state(session, chat_id=chat_id, telegram_user_id=telegram_user_id, state=state)
            return BotResponsePlan(
                text=self._build_multi_plan_confirmation_text(state),
                reply_markup=self._build_confirmation_keyboard(),
            )

        learning_context = self.self_learning.prepare(
            session,
            telegram_user_id=telegram_user_id,
            message_text=message_text,
            base_confidence=0.55,
        )
        prepared_message_text = learning_context.prepared_message

        if confirmation_state is not None:
            if lowered in {"yes", "y", "confirm", "confirm it", "ok", "okay"} or route.route == "confirmation_reply":
                return self._execute_confirmation_state(
                    session,
                    confirmation_state=confirmation_state,
                    pref=pref,
                    chat_id=chat_id,
                    telegram_user_id=telegram_user_id,
                    open_reminders=open_reminders,
                )
            if lowered in {"no", "n", "cancel", "stop"}:
                signature = self.self_learning.build_signature(confirmation_state.source_message_text or message_text)
                self.self_learning.record_correction(session, telegram_user_id=telegram_user_id, signature=signature, notes="user_cancelled_confirmation")
                self._clear_confirmation_state(session, chat_id=chat_id)
                self._clear_thread_state(session, chat_id=chat_id)
                return BotResponsePlan(text="Okay — I cancelled that pending action.")
            if route.route == 'repair_conversation' or detect_repair_signal(prepared_message_text) is not None or repair_rewrite is not None:
                pending = PendingConversationState(
                    action=confirmation_state.action,
                    reminder=confirmation_state.envelope.reminder.model_copy(deep=True),
                    target=confirmation_state.envelope.target.model_copy(deep=True),
                    preferences=confirmation_state.envelope.preferences.model_copy(deep=True),
                    follow_up=FollowUp(needed=True, question='Understood — what should I change? You can reply with a new time like "2 PM" or a correction like "use tomorrow instead".', missing_fields=['time_phrase']),
                    user_message_summary='phase9_3_confirmation_repair',
                    source_message_text=confirmation_state.source_message_text or message_text,
                    follow_up_turns=1,
                )
                self._save_pending_state(session, chat_id=chat_id, telegram_user_id=telegram_user_id, state=pending)
                self._clear_confirmation_state(session, chat_id=chat_id)
                if repair_rewrite is not None and repair_rewrite.handled_as_follow_up and repair_rewrite.message_text not in {'__CHANGE_TIME_ONLY__', '__CHANGE_DATE_ONLY__'}:
                    prepared_message_text = repair_rewrite.message_text
                    cleaned = prepared_message_text
                    lowered = cleaned.lower()
                else:
                    return BotResponsePlan(text=pending.follow_up.question or 'What should I change?')

        repair_plan = self._try_repair_conversation(
            session,
            chat_id=chat_id,
            telegram_user_id=telegram_user_id,
            message_text=prepared_message_text,
            open_reminders=open_reminders,
        )
        if repair_plan is not None:
            return repair_plan

        learned_examples = self.example_memory.format_for_prompt(
            self.example_memory.find_similar(session, telegram_user_id=telegram_user_id, message_text=prepared_message_text)
        )
        for item in learning_context.risky_examples:
            if item.example.source_text:
                learned_examples.append(
                    f"risky_user='{item.example.source_text}' | action={item.example.action_name} | task='{item.example.resolved_task or ''}' | time='{item.example.resolved_time_phrase or ''}'"
                )
        matched_memory_profiles = self.memory_profiles.find_matches(session, telegram_user_id=telegram_user_id, message_text=prepared_message_text)
        memory_profile_lines = self.memory_profiles.format_for_prompt(matched_memory_profiles)
        fastpath_wake = self._build_wake_up_envelope(prepared_message_text)
        if fastpath_wake is not None:
            interpreter_result = InterpreterResult(
                envelope=fastpath_wake,
                raw_response_text=None,
                model_name='wake-up-fastpath',
                validation_ok=True,
                error_message=None,
            )
        else:
            interpreter_result = self.interpreter.interpret(
                message_text=prepared_message_text,
                timezone_name=pref.timezone,
                pending_state=pending_state,
                open_reminders=open_reminders,
                preference_snapshot=self.reminder_service.format_preferences_summary(pref),
                learned_examples=learned_examples,
                memory_profile_lines=memory_profile_lines,
            )
        envelope = interpreter_result.envelope
        if pending_state is not None and envelope.action == "clarify":
            envelope = self._merge_pending_state(pending_state, prepared_message_text)
        if envelope.action in {"clarify", "help"}:
            inferred = infer_indirect_reminder(prepared_message_text)
            if inferred is not None:
                envelope = inferred
        envelope = apply_semantic_judgment(prepared_message_text, envelope, pref.timezone)
        memory_reasoning = self.memory_reasoner.apply(
            envelope=envelope,
            message_text=prepared_message_text,
            matched_profiles=matched_memory_profiles,
        )
        envelope.confidence = memory_reasoning.adjusted_confidence
        for reason in memory_reasoning.reasons:
            if reason not in envelope.reasoning_tags:
                envelope.reasoning_tags.append(reason)
        if memory_reasoning.follow_up_text and not envelope.follow_up.needed:
            envelope.follow_up.needed = True
            envelope.follow_up.question = memory_reasoning.follow_up_text
            if 'datetime_text' not in envelope.follow_up.missing_fields:
                envelope.follow_up.missing_fields.append('datetime_text')
        if learning_context.confidence_adjustment.reasons:
            envelope.confidence = learning_context.confidence_adjustment.adjusted_confidence
            for reason in learning_context.confidence_adjustment.reasons:
                if reason not in envelope.reasoning_tags:
                    envelope.reasoning_tags.append(reason)
        if learning_context.risk_score >= 0.35 and "learned_risk" not in envelope.reasoning_tags:
            envelope.reasoning_tags.append("learned_risk")
            envelope.confidence = min(envelope.confidence, 0.62)
        if learning_context.applied_patterns and "learned_time_pattern" not in envelope.reasoning_tags:
            envelope.reasoning_tags.append("learned_time_pattern")
        checker_result = self.checker.check(envelope=envelope, open_reminders=open_reminders)
        evaluator_result = self.evaluator.evaluate(
            envelope=envelope,
            checker_result=checker_result,
            open_reminders=open_reminders,
            timezone_name=pref.timezone,
        )
        checker_result.confidence = evaluator_result.adjusted_confidence
        for tag in evaluator_result.reasoning_tags:
            if tag not in envelope.reasoning_tags:
                envelope.reasoning_tags.append(tag)

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

        if evaluator_result.follow_up_text or checker_result.follow_up_text or envelope.follow_up.needed:
            follow_up_text = evaluator_result.follow_up_text or checker_result.follow_up_text or envelope.follow_up.question or "I need a bit more information."
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

        if evaluator_result.force_confirmation or self._should_request_confirmation(
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
                confirmation_reason=(evaluator_result.confirmation_text if evaluator_result.force_confirmation and evaluator_result.confirmation_text else self._confirmation_reason(envelope, checker_result.confidence)),
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
            source_text=pending_state.source_message_text if pending_state and pending_state.source_message_text else prepared_message_text,
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
                self._record_success_learning(session, chat_id=chat_id, telegram_user_id=telegram_user_id, source_text=ai_run.message_text, action_name=envelope.action, task=deleted.task, time_phrase=None, learned_from_follow_up=False, notes="resolution_success")
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
            self._record_success_learning(session, chat_id=chat_id, telegram_user_id=telegram_user_id, source_text=ai_run.message_text, action_name=envelope.action, task=updated.task, time_phrase=envelope.reminder.datetime_text, learned_from_follow_up=False, notes="resolution_success")
        return status if updated is None else f"Updated reminder #{updated.id}."

    def handle_confirmation_choice(self, session, *, choice: str, chat_id: int, telegram_user_id: int) -> str:
        pref = self.reminder_service.get_or_create_preferences(
            session,
            chat_id=chat_id,
            telegram_user_id=telegram_user_id,
            timezone_name=self.settings.default_timezone,
        )
        plan_state = self._get_multi_plan_state(session, chat_id=chat_id)
        if plan_state is not None:
            if choice == "cancel":
                self._clear_multi_plan_state(session, chat_id=chat_id)
                return "Okay — I cancelled that multi-reminder plan."
            if choice == "edit":
                self._clear_multi_plan_state(session, chat_id=chat_id)
                return "Okay — send me the corrected multi-reminder request in one message."
            if choice == "confirm":
                plan = self._execute_multi_plan_state(
                    session,
                    state=plan_state,
                    chat_id=chat_id,
                    telegram_user_id=telegram_user_id,
                )
                return plan.text

        confirmation_state = self._get_confirmation_state(session, chat_id=chat_id)
        if confirmation_state is None:
            return "That confirmation was already used or has expired. Please send the request again if you still want to do it."
        if choice == "cancel":
            self.self_learning.record_correction(
                session,
                telegram_user_id=telegram_user_id,
                signature=self.self_learning.build_signature(confirmation_state.source_message_text or ""),
                notes="button_cancelled_confirmation",
            )
            self._clear_confirmation_state(session, chat_id=chat_id)
            self._clear_thread_state(session, chat_id=chat_id)
            return "Okay — I cancelled that pending action."
        if choice == "edit":
            self.self_learning.record_correction(
                session,
                telegram_user_id=telegram_user_id,
                signature=self.self_learning.build_signature(confirmation_state.source_message_text or ""),
                notes="button_requested_edit",
            )
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
        self.self_learning.record_confirmation(
            session,
            telegram_user_id=telegram_user_id,
            signature=self.self_learning.build_signature(confirmation_state.source_message_text or ""),
            confirmed=True,
            notes=confirmation_state.confirmation_reason or "confirmed",
        )
        self._clear_confirmation_state(session, chat_id=chat_id)
        self._clear_thread_state(session, chat_id=chat_id)
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
        self._clear_thread_state(session, chat_id=chat_id)
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
            list_text = self.list_tool.execute(session, chat_id=chat_id)
            suggestions = self.proactive_suggester.suggestions_for_list(open_reminders)
            if suggestions:
                list_text += "\n\nSuggestion: " + suggestions[0]
            return BotResponsePlan(text=list_text)

        if envelope.action == "today_agenda":
            self.feedback.record(session, chat_id=chat_id, telegram_user_id=telegram_user_id, message_text=original_message_text, phase="assistant", outcome="today_agenda")
            today_text = self.today_tool.execute(session, chat_id=chat_id, timezone_name=pref.timezone)
            today_items = self.reminder_service.list_today_reminders(session, chat_id=chat_id, timezone_name=pref.timezone)
            suggestions = self.proactive_suggester.suggestions_for_agenda(today_items)
            if suggestions:
                today_text += "\n\nSuggestion: " + suggestions[0]
            return BotResponsePlan(text=today_text)

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
                for reminder in reminders:
                    self.memory_profiles.remember_from_values(session, telegram_user_id=telegram_user_id, task=reminder.task, hour_local=reminder.hour_local, minute_local=reminder.minute_local, recurrence_type=reminder.recurrence_type, confirmed=was_confirmed)
                suggestions = self.proactive_suggester.suggestions_after_create(session, chat_id=chat_id, created_reminders=reminders, open_reminders=open_reminders)
                if suggestions:
                    text += "\n\nSuggestion: " + suggestions[0]
            else:
                self._record_failure_learning(session, chat_id=chat_id, telegram_user_id=telegram_user_id, message_text=original_message_text, action_name=envelope.action, details={"text": text})
            return BotResponsePlan(text=text)

        if envelope.action == "create_reminder":
            duplicates = self.duplicates.find_possible_duplicates(
                reminders=open_reminders,
                task=envelope.reminder.task or "",
                due_repr=envelope.reminder.datetime_text or "",
                recurrence=envelope.reminder.recurrence_text,
                timezone_name=pref.timezone,
            )
            if duplicates:
                dup = duplicates[0]
                if not was_confirmed:
                    self.feedback.record(session, chat_id=chat_id, telegram_user_id=telegram_user_id, message_text=original_message_text, phase="create", outcome="duplicate_block")
                    return BotResponsePlan(text=f"This looks very similar to reminder #{dup.id}: {dup.task}. Confirm first, or use /list to review your reminders.")
                self.audit.record_action(session, user_id=telegram_user_id, reminder_id=dup.id, action_name="create_reminder_idempotent", action_args_json=envelope.model_dump_json(), executor_result_json=json.dumps({"text": f"Reminder #{dup.id} already exists"}), status="success")
                return BotResponsePlan(text=f"I already have reminder #{dup.id} for that: {dup.task}. I did not create a duplicate.")
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
            self._record_success_learning(session, chat_id=chat_id, telegram_user_id=telegram_user_id, source_text=source_text or original_message_text, action_name=envelope.action, task=reminder.task, time_phrase=envelope.reminder.datetime_text, learned_from_follow_up=False, notes=("confirmed" if was_confirmed else "success"))
            self.memory_profiles.remember_from_values(session, telegram_user_id=telegram_user_id, task=reminder.task, hour_local=reminder.hour_local, minute_local=reminder.minute_local, recurrence_type=reminder.recurrence_type, confirmed=was_confirmed)
            self.reference_memory.remember(session, chat_id=chat_id, telegram_user_id=telegram_user_id, task=reminder.task, time_phrase=envelope.reminder.datetime_text, created_reminder_id=reminder.id)
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
                self._record_success_learning(session, chat_id=chat_id, telegram_user_id=telegram_user_id, source_text=original_message_text, action_name=envelope.action, task=deleted.task, time_phrase=None, learned_from_follow_up=False, notes=("confirmed" if was_confirmed else "success"))
                self.reference_memory.remember(session, chat_id=chat_id, telegram_user_id=telegram_user_id, referenced_reminder_id=deleted.id, task=deleted.task)
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
            self._record_success_learning(session, chat_id=chat_id, telegram_user_id=telegram_user_id, source_text=original_message_text, action_name=envelope.action, task=updated.task, time_phrase=envelope.reminder.datetime_text, learned_from_follow_up=False, notes=("confirmed" if was_confirmed else "success"))
            return BotResponsePlan(text=text)

        self._record_failure_learning(session, chat_id=chat_id, telegram_user_id=telegram_user_id, message_text=original_message_text, action_name="clarify", details={"reason": "unknown_action"})
        return BotResponsePlan(text="I couldn't understand that. Try /help.")

    def _should_request_confirmation(self, *, envelope: InterpretationEnvelope, interpreter_result: InterpreterResult, confidence: float) -> bool:
        if envelope.action not in {"create_reminder", "update_reminder", "delete_reminder", "deadline_chain"}:
            return False
        if should_confirm_for_semantics(envelope):
            return True
        if "learned_risk" in envelope.reasoning_tags:
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
        if "learned_risk" in tags:
            return "learned_risk"
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
        if state.confirmation_reason and state.confirmation_reason.startswith('I understood this as'):
            return state.confirmation_reason
        semantic_text = build_semantic_confirmation_text(envelope)
        if semantic_text:
            return semantic_text
        if "learned_risk" in set(envelope.reasoning_tags):
            return "I've seen similar messages need correction before. Please confirm before I schedule it."
        task = envelope.reminder.task or "this reminder"
        when = normalize_time_phrase(envelope.reminder.datetime_text or "that time")
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



    def handle_calendar_screenshot_import(self, session, *, chat_id: int, telegram_user_id: int, image_path: str, caption_text: str | None) -> BotResponsePlan:
        if not self.settings.calendar_import_enabled:
            return BotResponsePlan(text="Calendar screenshot import is disabled for this bot.")
        try:
            proposal = self.calendar_importer.import_from_image(image_path, caption_text=caption_text)
        except CalendarImportError as exc:
            self.feedback.record(session, chat_id=chat_id, telegram_user_id=telegram_user_id, message_text=caption_text or '[calendar screenshot]', phase='calendar_import', outcome='failed')
            return BotResponsePlan(text=str(exc))

        items = [
            MultiPlanItem(task=f"Meeting: {meeting.title}", time_phrase=meeting.reminder_time_phrase, requires_ack=False)
            for meeting in proposal.meetings
        ]
        state = MultiPlanConfirmationState(
            source_message_text=caption_text or 'calendar screenshot import',
            items=items,
            confidence=0.9,
            shared_context=proposal.day_hint,
        )
        self._save_multi_plan_state(session, chat_id=chat_id, telegram_user_id=telegram_user_id, state=state)
        self.feedback.record(session, chat_id=chat_id, telegram_user_id=telegram_user_id, message_text=caption_text or '[calendar screenshot]', phase='calendar_import', outcome='proposal_ready')
        self.reference_memory.remember(session, chat_id=chat_id, telegram_user_id=telegram_user_id, task=items[0].task if items else None, time_phrase=items[0].time_phrase if items else None)
        return BotResponsePlan(text=proposal.confirmation_text())

    def _get_pending_state(self, session, *, chat_id: int) -> PendingConversationState | None:
        stmt = select(ConversationState).where(ConversationState.chat_id == chat_id)
        row = session.scalar(stmt)
        if row is None or row.pending_intent != "phase6_follow_up":
            return None
        try:
            return PendingConversationState.model_validate_json(row.state_json)
        except Exception:
            return None

    def _build_multi_plan_confirmation_text(self, state: MultiPlanConfirmationState) -> str:
        lines = [
            'I found multiple reminders in your message. Confirm and I will create all of them:',
            *[f"• {item.task} — {item.time_phrase}" for item in state.items],
        ]
        return "\n".join(lines)

    def _get_multi_plan_state(self, session, *, chat_id: int) -> MultiPlanConfirmationState | None:
        stmt = select(ConversationState).where(ConversationState.chat_id == chat_id)
        row = session.scalar(stmt)
        if row is None or row.pending_intent != "phase9_plan_confirm":
            return None
        try:
            return MultiPlanConfirmationState.model_validate_json(row.state_json)
        except Exception:
            return None

    def _save_multi_plan_state(self, session, *, chat_id: int, telegram_user_id: int, state: MultiPlanConfirmationState) -> None:
        stmt = select(ConversationState).where(ConversationState.chat_id == chat_id)
        row = session.scalar(stmt)
        payload = state.model_dump_json()
        if row is None:
            row = ConversationState(chat_id=chat_id, telegram_user_id=telegram_user_id, pending_intent="phase9_plan_confirm", state_json=payload)
            session.add(row)
        else:
            row.telegram_user_id = telegram_user_id
            row.pending_intent = "phase9_plan_confirm"
            row.state_json = payload
        session.commit()

    def _clear_multi_plan_state(self, session, *, chat_id: int) -> None:
        stmt = select(ConversationState).where(ConversationState.chat_id == chat_id)
        row = session.scalar(stmt)
        if row is not None and row.pending_intent == "phase9_plan_confirm":
            session.delete(row)
            session.commit()

    def _execute_multi_plan_state(self, session, *, state: MultiPlanConfirmationState, chat_id: int, telegram_user_id: int) -> BotResponsePlan:
        pref = self.reminder_service.get_or_create_preferences(
            session,
            chat_id=chat_id,
            telegram_user_id=telegram_user_id,
            timezone_name=self.settings.default_timezone,
        )
        open_reminders = self.reminder_service.list_open_reminders(session, chat_id=chat_id)
        created = []
        lines = []
        for item in state.items:
            reminder, status = self.create_tool.execute(
                session,
                scheduler=self.scheduler,
                incoming_text=state.source_message_text,
                telegram_user_id=telegram_user_id,
                chat_id=chat_id,
                timezone_name=pref.timezone,
                task=item.task,
                time_phrase=item.time_phrase,
                requires_ack=item.requires_ack,
                retry_interval_minutes=pref.wakeup_retry_interval_minutes,
                max_attempts=pref.wakeup_max_attempts,
                source_mode="phase9-plan",
                interpretation_json=None,
                target_selector_json=None,
                ai_confidence=state.confidence,
            )
            if reminder is not None:
                created.append(reminder)
                lines.append(f"• Created reminder #{reminder.id}: {reminder.task}")
                self.memory_profiles.remember_from_values(session, telegram_user_id=telegram_user_id, task=reminder.task, hour_local=reminder.hour_local, minute_local=reminder.minute_local, recurrence_type=reminder.recurrence_type, confirmed=True)
            else:
                lines.append(f"• Could not create '{item.task}' — {status}")
        self._clear_multi_plan_state(session, chat_id=chat_id)
        self._clear_thread_state(session, chat_id=chat_id)
        suggestions = self.proactive_suggester.suggestions_after_create(session, chat_id=chat_id, created_reminders=created, open_reminders=open_reminders)
        text = "Done — here is your plan:\n" + "\n".join(lines) if lines else "I couldn't create that plan."
        if suggestions:
            text += "\n\nSuggestion: " + suggestions[0]
        return BotResponsePlan(text=text)

    def _get_thread_state(self, session, *, chat_id: int) -> ThreadConversationState | None:
        return self.thread_memory.get(session, chat_id=chat_id)

    def _save_thread_state(self, session, *, chat_id: int, telegram_user_id: int, state: ThreadConversationState) -> None:
        self.thread_memory.save(session, chat_id=chat_id, telegram_user_id=telegram_user_id, state=state)

    def _clear_thread_state(self, session, *, chat_id: int) -> None:
        self.thread_memory.clear(session, chat_id=chat_id)

    def _conversation_task_time(self, *, pending_state, confirmation_state, reference_state, open_reminders: list) -> tuple[str | None, str | None]:
        if pending_state is not None:
            return pending_state.reminder.task, pending_state.reminder.datetime_text
        if confirmation_state is not None:
            return confirmation_state.envelope.reminder.task, confirmation_state.envelope.reminder.datetime_text
        if reference_state.last_discussed_task or reference_state.last_discussed_time_phrase:
            return reference_state.last_discussed_task, reference_state.last_discussed_time_phrase
        if open_reminders:
            latest = max(open_reminders, key=lambda reminder: reminder.id)
            return latest.task, None
        return None, None

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
        self.self_learning.record_correction(
            session,
            telegram_user_id=telegram_user_id,
            signature=self.self_learning.build_signature(latest.original_text or latest.task),
            notes="repair_signal_detected",
        )
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


    def _handle_tool_first_route(self, session, *, chat_id: int, telegram_user_id: int, message_text: str, lowered: str, pref, open_reminders: list, reference_state: ChatReferenceState) -> tuple[BotResponsePlan | None, str]:
        route = self.tool_router.detect(message_text)
        cleaned = message_text
        if route.kind == "list_all":
            text = self.list_tool.execute(session, chat_id=chat_id)
            self.reference_memory.remember(session, chat_id=chat_id, telegram_user_id=telegram_user_id, listed_reminder_ids=[r.id for r in open_reminders])
            return BotResponsePlan(text=text), cleaned
        if route.kind == "list_today":
            reminders = self.reminder_service.list_today_reminders(session, chat_id=chat_id, timezone_name=pref.timezone)
            self.reference_memory.remember(session, chat_id=chat_id, telegram_user_id=telegram_user_id, listed_reminder_ids=[r.id for r in reminders])
            return BotResponsePlan(text=self.today_tool.execute(session, chat_id=chat_id, timezone_name=pref.timezone)), cleaned
        if route.kind == "list_tomorrow":
            return BotResponsePlan(text=self._render_tomorrow(session, chat_id=chat_id, timezone_name=pref.timezone, telegram_user_id=telegram_user_id)), cleaned
        if route.kind == "prefs":
            return BotResponsePlan(text=self.reminder_service.format_preferences_summary(pref)), cleaned
        if route.kind == "missed":
            return BotResponsePlan(text=self.missed_tool.execute(session, chat_id=chat_id)), cleaned

        available_sorted_ids = [r.id for r in open_reminders]
        target_id = self.reference_resolver.extract_target_id(message_text, available_sorted_ids)
        if target_id is None:
            target_id = self.reference_resolver.extract_task_reference(message_text, open_reminders)

        if route.kind in {"delete_like", "update_like"} and target_id is None and open_reminders:
            ambiguous = self.repair_clarifier.build_reference_clarification(text=message_text, candidate_reminders=open_reminders)
            if ambiguous is not None:
                pending = PendingConversationState(
                    action='delete_reminder' if route.kind == 'delete_like' else 'update_reminder',
                    reminder=ReminderDraft(),
                    target=TargetSelector(),
                    preferences=PreferencePatch(),
                    follow_up=FollowUp(needed=True, question=ambiguous.text, missing_fields=['target']),
                    user_message_summary='phase9_3_reference_clarification',
                    source_message_text=message_text,
                    follow_up_turns=1,
                )
                self._save_pending_state(session, chat_id=chat_id, telegram_user_id=telegram_user_id, state=pending)
                return BotResponsePlan(text=ambiguous.text), cleaned

        if route.kind == "delete_like" and target_id is not None:
            cleaned = self.reference_resolver.build_delete_rewrite(message_text, target_id)
            self.reference_memory.remember(session, chat_id=chat_id, telegram_user_id=telegram_user_id, referenced_reminder_id=target_id)
        elif route.kind == "update_like" and target_id is not None:
            cleaned = self.reference_resolver.build_update_rewrite(message_text, target_id)
            self.reference_memory.remember(session, chat_id=chat_id, telegram_user_id=telegram_user_id, referenced_reminder_id=target_id)
        elif route.kind == "create_like":
            cleaned = self.reference_resolver.substitute_pronoun_create(message_text, ReferenceContext(
                last_discussed_task=reference_state.last_discussed_task,
                last_discussed_time_phrase=reference_state.last_discussed_time_phrase,
                last_created_reminder_id=reference_state.last_created_reminder_id,
                last_listed_reminder_ids=reference_state.last_listed_reminder_ids,
                last_referenced_reminder_id=reference_state.last_referenced_reminder_id,
            ))
        return None, cleaned

    def _render_tomorrow(self, session, *, chat_id: int, timezone_name: str, telegram_user_id: int) -> str:
        start_utc, _ = local_day_bounds_utc(timezone_name=timezone_name)
        tomorrow_start = start_utc + timedelta(days=1)
        tomorrow_end = tomorrow_start + timedelta(days=1)
        reminders = [
            r for r in self.reminder_service.list_open_reminders(session, chat_id=chat_id)
            if r.next_run_at_utc is not None and tomorrow_start.replace(tzinfo=None) <= r.next_run_at_utc < tomorrow_end.replace(tzinfo=None)
        ]
        self.reference_memory.remember(session, chat_id=chat_id, telegram_user_id=telegram_user_id, listed_reminder_ids=[r.id for r in reminders])
        if not reminders:
            return "You have no upcoming reminders for tomorrow."
        lines = ["Tomorrow's reminders:"]
        for reminder in reminders:
            when_label = format_dt_for_user(reminder.next_run_at_utc, reminder.timezone) if reminder.next_run_at_utc else "not scheduled"
            lines.append(f"• {reminder_summary_line(reminder, when_label)}")
        return "\n".join(lines)

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

    def _record_success_learning(self, session, *, chat_id: int, telegram_user_id: int, source_text: str, action_name: str, task: str | None, time_phrase: str | None, learned_from_follow_up: bool, notes: str | None = None) -> None:
        self.feedback.record(session, chat_id=chat_id, telegram_user_id=telegram_user_id, message_text=source_text, phase=action_name, outcome="success", details={"task": task, "time_phrase": time_phrase, "notes": notes})
        self.example_memory.remember(session, chat_id=chat_id, telegram_user_id=telegram_user_id, source_text=source_text, action_name=action_name, resolved_task=task, resolved_time_phrase=time_phrase, learned_from_follow_up=learned_from_follow_up, notes=notes)
        self.self_learning.record_success(session, telegram_user_id=telegram_user_id, signature=self.self_learning.build_signature(source_text), notes=notes or "success")
        if time_phrase:
            self.rule_suggester.remember_time_phrase(session, raw_phrase=time_phrase)

    def _record_failure_learning(self, session, *, chat_id: int, telegram_user_id: int, message_text: str, action_name: str, details: dict) -> None:
        self.feedback.record(session, chat_id=chat_id, telegram_user_id=telegram_user_id, message_text=message_text, phase=action_name, outcome="failure", error_code="execution_failure", details=details)
        if any(key in json.dumps(details) for key in ("2 AM", "2AM", "ambiguous", "suspicious")):
            self.self_learning.record_correction(session, telegram_user_id=telegram_user_id, signature=self.self_learning.build_signature(message_text), notes="failure_needs_confirmation")
        if self.settings.ai_enable_eval_logging:
            self.eval_builder.add_candidate(session, label=f"auto::{action_name}", input_text=message_text, expected_action="create_reminder" if action_name == "create_reminder" else "clarify", expected_json=details)
