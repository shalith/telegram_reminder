from __future__ import annotations

import logging
import time

from telegram.error import Conflict, NetworkError, RetryAfter, TimedOut

from telegram import Update

from app.config import Settings
from app.db import init_db
from app.health_server import HealthServer
from app.logging_setup import setup_logging
from app.runtime import RuntimeState
from app.scheduler import ReminderScheduler
from app.telegram_bot import TelegramReminderBot
from app.telemetry.otel import init_otel

logger = logging.getLogger(__name__)


def main() -> None:
    settings = Settings.from_env()
    setup_logging(log_level=settings.log_level, log_dir=settings.log_dir, json_logs=settings.json_logs)
    init_db(settings.database_url)
    if settings.otel_enabled:
        init_otel("telegram-reminder-mvp-phase6", settings.otel_exporter_endpoint)

    runtime_state = RuntimeState()
    reminder_scheduler = ReminderScheduler(settings, runtime_state)
    health_server = HealthServer(
        host=settings.health_host,
        port=settings.health_port,
        runtime_state=runtime_state,
        scheduler_running=reminder_scheduler.is_running,
        scheduler_job_count=reminder_scheduler.job_count,
    )

    logger.info("Application starting", extra={"event": "app_start", "status": settings.database_url})
    reminder_scheduler.start()
    reminder_scheduler.restore_pending_jobs()
    health_server.start()

    application = TelegramReminderBot(settings, reminder_scheduler, runtime_state).build()

    try:
        conflict_attempt = 0
        recoverable_attempt = 0
        while True:
            try:
                application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)
                break
            except Conflict as exc:  # pragma: no cover
                conflict_attempt += 1
                runtime_state.record_error(str(exc))
                wait_seconds = min(30, 5 * conflict_attempt)
                logger.warning(
                    "Polling conflict detected; another bot instance may still be running. Retrying.",
                    extra={"event": "polling_conflict", "attempt": conflict_attempt, "wait_seconds": wait_seconds},
                )
                time.sleep(wait_seconds)
                continue
            except (NetworkError, TimedOut, RetryAfter) as exc:  # pragma: no cover
                recoverable_attempt += 1
                runtime_state.record_error(str(exc))
                wait_seconds = min(60, max(5, 4 * recoverable_attempt))
                logger.warning(
                    "Recoverable Telegram polling error detected; restarting polling loop.",
                    extra={"event": "polling_recoverable_error", "attempt": recoverable_attempt, "wait_seconds": wait_seconds},
                )
                time.sleep(wait_seconds)
                continue
    except Exception as exc:  # pragma: no cover
        runtime_state.record_error(str(exc))
        logger.exception("Application crashed", extra={"event": "app_crash"})
        raise
    finally:
        health_server.stop()
        reminder_scheduler.shutdown()
        logger.info("Application stopped", extra={"event": "app_stop"})


if __name__ == "__main__":
    main()
