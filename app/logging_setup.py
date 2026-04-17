from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "event"):
            payload["event"] = getattr(record, "event")
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key in ("chat_id", "reminder_id", "job_id", "path", "status"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, ensure_ascii=False)


class HumanFormatter(logging.Formatter):
    pass


def setup_logging(*, log_level: str, log_dir: str, json_logs: bool) -> None:
    level = getattr(logging, log_level.upper(), logging.INFO)
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    stream_handler = logging.StreamHandler()
    file_handler = RotatingFileHandler(
        filename=str(Path(log_dir) / "reminder_bot.log"),
        maxBytes=1_500_000,
        backupCount=5,
        encoding="utf-8",
    )

    if json_logs:
        formatter: logging.Formatter = JsonFormatter()
    else:
        formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    stream_handler.setFormatter(formatter)
    file_handler.setFormatter(JsonFormatter())

    root.addHandler(stream_handler)
    root.addHandler(file_handler)
