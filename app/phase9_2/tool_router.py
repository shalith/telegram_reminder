from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

RouteKind = Literal["none", "list_all", "list_today", "list_tomorrow", "prefs", "missed", "create_like", "update_like", "delete_like"]


@dataclass(slots=True)
class ToolRouteDecision:
    kind: RouteKind = "none"
    reason: str | None = None


class ToolFirstRouter:
    LIST_ALL_PATTERNS = (
        "list my reminders", "show my reminders", "what are my reminders", "what do i have",
        "existing agenda", "list my agenda", "show my agenda", "what is my agenda",
        "what are my existing agenda", "list my reminder", "show reminder list",
    )
    LIST_TODAY_PATTERNS = (
        "list today's reminder", "list todays reminder", "list today's reminders", "list todays reminders",
        "list today reminder", "list today reminders", "what do i have today", "today agenda",
        "today's agenda", "todays agenda", "today reminder", "today reminders", "do i have reminders for today",
        "do i have anything today", "what's on today", "whats on today",
    )
    LIST_TOMORROW_PATTERNS = (
        "list tomorrow reminders", "list tomorrow reminder", "tomorrow reminders", "tomorrow reminder",
        "what do i have tomorrow", "do i have anything tomorrow", "do i have reminders for tomorrow",
        "tomorrow agenda", "tomorrow's agenda", "list tomorrow agenda",
    )

    def detect(self, text: str) -> ToolRouteDecision:
        lowered = " ".join((text or "").strip().lower().split())
        if not lowered:
            return ToolRouteDecision()
        if lowered in {"/prefs", "prefs", "preferences", "show preferences", "show my preferences"}:
            return ToolRouteDecision("prefs", "preferences_request")
        if any(p in lowered for p in self.LIST_TODAY_PATTERNS):
            return ToolRouteDecision("list_today", "today_query")
        if any(p in lowered for p in self.LIST_TOMORROW_PATTERNS):
            return ToolRouteDecision("list_tomorrow", "tomorrow_query")
        # pure agenda/list requests should not go to general chat
        if any(p in lowered for p in self.LIST_ALL_PATTERNS):
            return ToolRouteDecision("list_all", "list_query")
        if lowered.startswith(("what are my existing agenda", "what is my existing agenda", "what are my agenda")):
            return ToolRouteDecision("list_all", "agenda_query")
        if lowered.startswith(("remove ", "delete ", "cancel ")) and ("reminder" in lowered or re.search(r"\b#?\d+\b", lowered)):
            return ToolRouteDecision("delete_like", "delete_request")
        if lowered.startswith(("update ", "change ", "rename ", "edit ")) and ("reminder" in lowered or re.search(r"\b#?\d+\b", lowered)):
            return ToolRouteDecision("update_like", "update_request")
        if lowered.startswith(("remind me it", "remind me that", "remind me this", "remind me the same")):
            return ToolRouteDecision("create_like", "pronoun_create")
        return ToolRouteDecision()
