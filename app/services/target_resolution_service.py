from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.ai.resolver import TargetResolver
from app.repositories.resolution_repo import ResolutionRepository
from app.recurrence import recurrence_label


class TargetResolutionService:
    def __init__(self):
        self.resolver = TargetResolver()
        self.repo = ResolutionRepository()

    def resolve(self, *, session, ai_run_id: int, action_name: str, selector_text: str | None, reminder_id: int | None, reminders: list):
        result = self.resolver.resolve(selector_text=selector_text, reminder_id=reminder_id, reminders=reminders)
        if result.status == "ambiguous" and result.candidates:
            self.repo.save_candidates(session, ai_run_id=ai_run_id, action_name=action_name, candidates=result.candidates)
        return result

    def build_keyboard(self, *, ai_run_id: int, candidates: list) -> InlineKeyboardMarkup:
        rows = []
        for candidate in candidates:
            reminder = candidate.reminder
            label = f"#{reminder.id} • {reminder.task} • {recurrence_label(reminder)}"
            rows.append([InlineKeyboardButton(label[:55], callback_data=f"resolve:{ai_run_id}:{reminder.id}")])
        return InlineKeyboardMarkup(rows)
