from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime

from dotenv import load_dotenv


load_dotenv()


@dataclass(slots=True)
class Settings:
    telegram_bot_token: str
    default_timezone: str = "Asia/Singapore"
    database_url: str = "sqlite:///./reminders.db"
    groq_api_key: str | None = None
    groq_model: str = "meta-llama/llama-4-scout-17b-16e-instruct"
    log_level: str = "INFO"
    log_dir: str = "./logs"
    json_logs: bool = True
    health_host: str = "127.0.0.1"
    health_port: int = 8088
    backup_dir: str = "./backups"
    ai_min_auto_execute_confidence: float = 0.82
    ai_confirmation_min_confidence: float = 0.45
    ai_confirm_wakeups: bool = True
    ai_enable_eval_logging: bool = True
    ai_enable_resolution_buttons: bool = True
    ai_memory_enabled: bool = True
    ai_memory_max_profiles: int = 3
    calendar_import_enabled: bool = True
    calendar_import_lead_minutes: int = 10
    calendar_import_fallback_to_today: bool = True
    otel_enabled: bool = False
    otel_exporter_endpoint: str | None = None

    @property
    def groq_enabled(self) -> bool:
        return bool(self.groq_api_key)

    @property
    def timestamp_for_filenames(self) -> str:
        return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError(
                "TELEGRAM_BOT_TOKEN is required. Copy .env.example to .env and set your token."
            )

        groq_key = os.getenv("GROQ_API_KEY", "").strip() or None
        groq_model = os.getenv(
            "GROQ_MODEL",
            "meta-llama/llama-4-scout-17b-16e-instruct",
        ).strip() or "meta-llama/llama-4-scout-17b-16e-instruct"

        return cls(
            telegram_bot_token=token,
            default_timezone=os.getenv("DEFAULT_TIMEZONE", "Asia/Singapore").strip() or "Asia/Singapore",
            database_url=os.getenv("DATABASE_URL", "sqlite:///./reminders.db").strip() or "sqlite:///./reminders.db",
            groq_api_key=groq_key,
            groq_model=groq_model,
            log_level=os.getenv("LOG_LEVEL", "INFO").strip() or "INFO",
            log_dir=os.getenv("LOG_DIR", "./logs").strip() or "./logs",
            json_logs=os.getenv("JSON_LOGS", "true").strip().lower() not in {"0", "false", "no"},
            health_host=os.getenv("HEALTH_HOST", "127.0.0.1").strip() or "127.0.0.1",
            health_port=int(os.getenv("HEALTH_PORT", "8088")),
            backup_dir=os.getenv("BACKUP_DIR", "./backups").strip() or "./backups",
            ai_min_auto_execute_confidence=float(os.getenv("AI_MIN_AUTO_EXECUTE_CONFIDENCE", "0.82")),
            ai_confirmation_min_confidence=float(os.getenv("AI_CONFIRMATION_MIN_CONFIDENCE", "0.45")),
            ai_confirm_wakeups=os.getenv("AI_CONFIRM_WAKEUPS", "true").strip().lower() not in {"0", "false", "no"},
            ai_enable_eval_logging=os.getenv("AI_ENABLE_EVAL_LOGGING", "true").strip().lower() not in {"0", "false", "no"},
            ai_enable_resolution_buttons=os.getenv("AI_ENABLE_RESOLUTION_BUTTONS", "true").strip().lower() not in {"0", "false", "no"},
            ai_memory_enabled=os.getenv("AI_MEMORY_ENABLED", "true").strip().lower() not in {"0", "false", "no"},
            ai_memory_max_profiles=int(os.getenv("AI_MEMORY_MAX_PROFILES", "3")),
            calendar_import_enabled=os.getenv("CALENDAR_IMPORT_ENABLED", "true").strip().lower() not in {"0", "false", "no"},
            calendar_import_lead_minutes=int(os.getenv("CALENDAR_IMPORT_LEAD_MINUTES", "10")),
            calendar_import_fallback_to_today=os.getenv("CALENDAR_IMPORT_FALLBACK_TO_TODAY", "true").strip().lower() not in {"0", "false", "no"},
            otel_enabled=os.getenv("OTEL_ENABLED", "false").strip().lower() in {"1", "true", "yes"},
            otel_exporter_endpoint=os.getenv("OTEL_EXPORTER_ENDPOINT", "").strip() or None,
        )
