from __future__ import annotations

from app.ai.normalizer import build_semantic_key, normalize_task


class DuplicateDetectionService:
    def find_possible_duplicates(self, *, reminders: list, task: str, due_repr: str, recurrence: str | None = None) -> list:
        normalized = normalize_task(task)
        key = build_semantic_key(task, due_repr, recurrence)
        matches = []
        for reminder in reminders:
            if reminder.normalized_task and reminder.normalized_task == normalized:
                matches.append(reminder)
                continue
            if reminder.semantic_key and reminder.semantic_key == key:
                matches.append(reminder)
        return matches
