from __future__ import annotations

import hashlib
import re


def collapse_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def normalize_task(task: str | None) -> str:
    if not task:
        return ""
    text = collapse_spaces(task).lower()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    return collapse_spaces(text)


def normalize_selector(selector_text: str | None) -> str:
    if not selector_text:
        return ""
    text = collapse_spaces(selector_text).lower()
    text = text.replace("the ", "")
    text = re.sub(r"[^a-z0-9\s#]", "", text)
    return collapse_spaces(text)


def build_semantic_key(task: str | None, due_repr: str | None, recurrence: str | None) -> str:
    raw = "|".join([normalize_task(task), collapse_spaces(due_repr or "").lower(), collapse_spaces(recurrence or "").lower()])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]
