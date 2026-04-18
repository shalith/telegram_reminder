from __future__ import annotations

import re
from dataclasses import dataclass


ORDINAL_WORDS = {
    "first": 1, "1st": 1,
    "second": 2, "2nd": 2,
    "third": 3, "3rd": 3,
    "fourth": 4, "4th": 4,
    "fifth": 5, "5th": 5,
}


@dataclass(slots=True)
class ReferenceContext:
    last_discussed_task: str | None = None
    last_discussed_time_phrase: str | None = None
    last_created_reminder_id: int | None = None
    last_listed_reminder_ids: list[int] | None = None
    last_referenced_reminder_id: int | None = None


class ReferenceResolver:
    def substitute_pronoun_create(self, text: str, ctx: ReferenceContext) -> str:
        lowered = " ".join((text or "").strip().lower().split())
        if lowered.startswith(("remind me it", "remind me that", "remind me this", "remind me the same")) and ctx.last_discussed_task:
            suffix = text.strip()[len(text.strip().split()[0]) + 1:]  # not used
            remainder = re.sub(r"^remind me\s+(it|that|this|the same)", "", text, flags=re.IGNORECASE).strip()
            rebuilt = f"Remind me {ctx.last_discussed_task}"
            if remainder and remainder.lower() not in {"it", "that", "this", "the same"}:
                rebuilt += f" {remainder}"
            elif ctx.last_discussed_time_phrase:
                rebuilt += f" {ctx.last_discussed_time_phrase}"
            return rebuilt
        return text

    def extract_target_id(self, text: str, available_ids: list[int]) -> int | None:
        lowered = " ".join((text or "").strip().lower().split())
        m = re.search(r"(?:reminder\s*)?#?(\d+)", lowered)
        if m:
            reminder_id = int(m.group(1))
            return reminder_id if reminder_id in available_ids else None
        for token, idx in ORDINAL_WORDS.items():
            if re.search(rf"{re.escape(token)}", lowered):
                if 1 <= idx <= len(available_ids):
                    return available_ids[idx - 1]
        if "latest reminder" in lowered or "last reminder" in lowered:
            return available_ids[0] if available_ids else None
        return None

    def extract_task_reference(self, text: str, available) -> int | None:
        lowered = " ".join((text or "").strip().lower().split())
        if "wake-up" in lowered or "wake up" in lowered:
            for reminder in available:
                if "wake up" in (reminder.task or "").lower():
                    return reminder.id
        task_match = re.search(r"the\s+(.+?)\s+reminder", lowered)
        if task_match:
            hint = task_match.group(1).strip()
            for reminder in available:
                if hint and hint in (reminder.task or "").lower():
                    return reminder.id
        return None

    def build_update_rewrite(self, text: str, target_id: int | None) -> str:
        if target_id is None:
            return text
        lowered = text.lower()
        if lowered.startswith(("update ", "change ", "edit ", "rename ")):
            match = re.match(r"^(?:update|change|edit|rename)(?:\s+the)?(?:\s+.+?reminder|\s+reminder\s*#?\d+|\s+#?\d+)?\s+(?:as|to)\s+(.+)$", text, re.IGNORECASE)
            if match:
                return f"update #{target_id} to {match.group(1).strip()}"
        if re.match(r"^\d+(?:st|nd|rd|th)\s+reminder\s+is\s+.+$", lowered):
            rest = re.sub(r"^\d+(?:st|nd|rd|th)\s+reminder\s+is\s+", "", text, flags=re.IGNORECASE)
            return f"update #{target_id} to {rest.strip()}"
        return text

    def build_delete_rewrite(self, text: str, target_id: int | None) -> str:
        if target_id is None:
            return text
        lowered = text.lower()
        if lowered.startswith(("remove ", "delete ", "cancel ")):
            return f"delete #{target_id}"
        return text
