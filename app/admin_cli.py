from __future__ import annotations

import argparse
import csv
import json
import shutil
from pathlib import Path

from app.config import Settings
from app.db import init_db, get_session
from app.models import Reminder, UserPreference
from app.service import ReminderService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reminder bot admin utilities")
    sub = parser.add_subparsers(dest="command", required=True)

    backup = sub.add_parser("backup-db", help="Create a timestamped copy of the SQLite database")
    backup.add_argument("--output-dir", default=None)

    export = sub.add_parser("export-reminders", help="Export reminders to JSON or CSV")
    export.add_argument("--format", choices=["json", "csv"], default="json")
    export.add_argument("--output", required=True)
    export.add_argument("--chat-id", type=int, default=None)
    export.add_argument("--status", default=None)

    prefs = sub.add_parser("export-preferences", help="Export preferences to JSON")
    prefs.add_argument("--output", required=True)

    return parser.parse_args()


def sqlite_path_from_url(database_url: str) -> Path:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise RuntimeError("backup-db currently supports SQLite databases only.")
    return Path(database_url[len(prefix):]).resolve()


def backup_db(settings: Settings, *, output_dir: str | None) -> None:
    db_path = sqlite_path_from_url(settings.database_url)
    if not db_path.exists():
        raise RuntimeError(f"Database file not found: {db_path}")
    target_dir = Path(output_dir or settings.backup_dir).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    destination = target_dir / f"reminders_backup_{settings.timestamp_for_filenames}.db"
    shutil.copy2(db_path, destination)
    print(destination)


def export_reminders(*, output: str, fmt: str, chat_id: int | None, status: str | None) -> None:
    service = ReminderService()
    with get_session() as session:
        reminders = service.list_all_reminders(session, chat_id=chat_id, status=status)
    rows = [
        {
            "id": item.id,
            "chat_id": item.chat_id,
            "telegram_user_id": item.telegram_user_id,
            "task": item.task,
            "status": item.status,
            "next_run_at_utc": item.next_run_at_utc.isoformat() if item.next_run_at_utc else None,
            "timezone": item.timezone,
            "recurrence_type": item.recurrence_type,
            "recurrence_day_of_week": item.recurrence_day_of_week,
            "requires_ack": item.requires_ack,
            "retry_interval_minutes": item.retry_interval_minutes,
            "max_attempts": item.max_attempts,
            "attempt_count": item.attempt_count,
            "group_token": item.group_token,
            "created_at": item.created_at.isoformat() if item.created_at else None,
        }
        for item in reminders
    ]
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "json":
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else [
                "id", "chat_id", "telegram_user_id", "task", "status", "next_run_at_utc", "timezone",
                "recurrence_type", "recurrence_day_of_week", "requires_ack", "retry_interval_minutes",
                "max_attempts", "attempt_count", "group_token", "created_at"
            ])
            writer.writeheader()
            writer.writerows(rows)
    print(path.resolve())


def export_preferences(*, output: str) -> None:
    with get_session() as session:
        prefs = session.query(UserPreference).order_by(UserPreference.chat_id.asc()).all()
    rows = [
        {
            "chat_id": item.chat_id,
            "telegram_user_id": item.telegram_user_id,
            "timezone": item.timezone,
            "default_snooze_minutes": item.default_snooze_minutes,
            "wakeup_retry_interval_minutes": item.wakeup_retry_interval_minutes,
            "wakeup_max_attempts": item.wakeup_max_attempts,
            "daily_agenda_enabled": item.daily_agenda_enabled,
            "daily_agenda_hour_local": item.daily_agenda_hour_local,
            "daily_agenda_minute_local": item.daily_agenda_minute_local,
            "missed_summary_enabled": item.missed_summary_enabled,
        }
        for item in prefs
    ]
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(path.resolve())


def main() -> None:
    settings = Settings.from_env()
    init_db(settings.database_url)
    args = parse_args()
    if args.command == "backup-db":
        backup_db(settings, output_dir=args.output_dir)
        return
    if args.command == "export-reminders":
        export_reminders(output=args.output, fmt=args.format, chat_id=args.chat_id, status=args.status)
        return
    if args.command == "export-preferences":
        export_preferences(output=args.output)
        return
    raise RuntimeError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    main()
