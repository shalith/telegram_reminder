from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher

from sqlalchemy import desc, select

from app.ai.normalizer import normalize_task
from app.models import TaskMemoryProfile


@dataclass(slots=True)
class MemoryMatch:
    score: float
    profile: TaskMemoryProfile


class MemoryProfileStore:
    def _classify_period(self, hour: int | None) -> str | None:
        if hour is None:
            return None
        if 5 <= hour <= 11:
            return 'morning'
        if 12 <= hour <= 16:
            return 'afternoon'
        if 17 <= hour <= 21:
            return 'evening'
        return 'night'

    def _display_time(self, hour: int | None, minute: int | None) -> str | None:
        if hour is None:
            return None
        minute = minute or 0
        suffix = 'AM' if hour < 12 else 'PM'
        display_hour = hour % 12 or 12
        return f"{display_hour}:{minute:02d} {suffix}"

    def remember_from_values(
        self,
        session,
        *,
        telegram_user_id: int,
        task: str | None,
        hour_local: int | None,
        minute_local: int | None,
        recurrence_type: str | None,
        confirmed: bool = False,
    ) -> TaskMemoryProfile | None:
        task_key = normalize_task(task)
        if not task_key:
            return None
        row = session.scalar(
            select(TaskMemoryProfile).where(
                TaskMemoryProfile.telegram_user_id == telegram_user_id,
                TaskMemoryProfile.task_key == task_key,
            )
        )
        if row is None:
            row = TaskMemoryProfile(
                telegram_user_id=telegram_user_id,
                task_key=task_key,
                sample_task=(task or task_key)[:255],
                preferred_hour_local=hour_local,
                preferred_minute_local=minute_local,
                preferred_time_of_day=self._classify_period(hour_local),
                preferred_recurrence_type=recurrence_type,
                use_count=0,
                confirmed_count=0,
            )
            session.add(row)
        row.use_count = int(row.use_count or 0) + 1
        if confirmed:
            row.confirmed_count = int(row.confirmed_count or 0) + 1
        if task and (not row.sample_task or len(task) < len(row.sample_task)):
            row.sample_task = task[:255]
        if hour_local is not None:
            prev = max(0, int(row.use_count or 1) - 1)
            if row.preferred_hour_local is None:
                row.preferred_hour_local = hour_local
                row.preferred_minute_local = minute_local or 0
            else:
                row.preferred_hour_local = int(round(((row.preferred_hour_local * prev) + hour_local) / max(1, prev + 1)))
                current_min = int(row.preferred_minute_local or 0)
                row.preferred_minute_local = int(round(((current_min * prev) + int(minute_local or 0)) / max(1, prev + 1)))
            row.preferred_time_of_day = self._classify_period(row.preferred_hour_local)
        if recurrence_type:
            row.preferred_recurrence_type = recurrence_type
        row.last_seen_at = datetime.now(UTC)
        session.commit()
        session.refresh(row)
        return row

    def find_matches(self, session, *, telegram_user_id: int, message_text: str, limit: int = 3) -> list[MemoryMatch]:
        stmt = (
            select(TaskMemoryProfile)
            .where(TaskMemoryProfile.telegram_user_id == telegram_user_id)
            .order_by(desc(TaskMemoryProfile.use_count), desc(TaskMemoryProfile.confirmed_count), desc(TaskMemoryProfile.last_seen_at))
            .limit(50)
        )
        rows = list(session.scalars(stmt).all())
        needle = normalize_task(message_text)
        if not needle:
            return []
        scored: list[MemoryMatch] = []
        for row in rows:
            hay = row.task_key or ''
            score = SequenceMatcher(None, needle, hay).ratio()
            if hay and hay in needle:
                score += 0.35
            elif needle and any(token in needle for token in hay.split() if len(token) > 2):
                score += 0.2
            if score >= 0.33:
                scored.append(MemoryMatch(score=score, profile=row))
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:limit]

    def format_for_prompt(self, matches: list[MemoryMatch]) -> list[str]:
        lines: list[str] = []
        for item in matches:
            p = item.profile
            time_text = self._display_time(p.preferred_hour_local, p.preferred_minute_local) or 'unknown time'
            lines.append(
                f"task='{p.sample_task or p.task_key}' | usually={time_text} | period={p.preferred_time_of_day or 'unknown'} | recurrence={p.preferred_recurrence_type or 'once'} | uses={int(p.use_count or 0)}"
            )
        return lines

    def suggest_time_text(self, profile: TaskMemoryProfile) -> str | None:
        return self._display_time(profile.preferred_hour_local, profile.preferred_minute_local)
