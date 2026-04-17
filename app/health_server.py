from __future__ import annotations

import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Callable
from urllib.parse import urlparse

from sqlalchemy import text

from app.db import get_session
from app.runtime import RuntimeState

logger = logging.getLogger(__name__)


class HealthServer:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        runtime_state: RuntimeState,
        scheduler_running: Callable[[], bool],
        scheduler_job_count: Callable[[], int],
    ) -> None:
        self.host = host
        self.port = port
        self.runtime_state = runtime_state
        self.scheduler_running = scheduler_running
        self.scheduler_job_count = scheduler_job_count
        self._server: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._server is not None:
            return

        runtime_state = self.runtime_state
        scheduler_running = self.scheduler_running
        scheduler_job_count = self.scheduler_job_count

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path == "/health":
                    payload, ok = build_health_payload(runtime_state, scheduler_running, scheduler_job_count)
                    self._json_response(HTTPStatus.OK if ok else HTTPStatus.SERVICE_UNAVAILABLE, payload)
                    return
                if parsed.path == "/metrics":
                    payload = build_metrics_payload(runtime_state, scheduler_job_count)
                    self._json_response(HTTPStatus.OK, payload)
                    return
                self._json_response(HTTPStatus.NOT_FOUND, {"status": "not_found"})

            def log_message(self, fmt: str, *args) -> None:  # noqa: A003
                logger.debug("health_server_access", extra={"event": "health_http_access", "path": self.path, "status": fmt % args if args else ""})

            def _json_response(self, status: HTTPStatus, payload: dict) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status.value)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = Thread(target=self._server.serve_forever, name="health-server", daemon=True)
        self._thread.start()
        logger.info(
            "Health server started",
            extra={"event": "health_server_started", "path": f"http://{self.host}:{self.port}/health"},
        )

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None
        self._thread = None
        logger.info("Health server stopped", extra={"event": "health_server_stopped"})


def db_healthcheck() -> tuple[bool, str | None]:
    try:
        with get_session() as session:
            session.execute(text("SELECT 1"))
        return True, None
    except Exception as exc:  # pragma: no cover - exercised in runtime
        return False, str(exc)


def build_health_payload(
    runtime_state: RuntimeState,
    scheduler_running: Callable[[], bool],
    scheduler_job_count: Callable[[], int],
) -> tuple[dict, bool]:
    runtime = runtime_state.as_dict()
    db_ok, db_error = db_healthcheck()
    sched_ok = scheduler_running()
    payload = {
        "status": "ok" if db_ok and sched_ok and runtime["bot_started"] else "degraded",
        "checks": {
            "database": {"ok": db_ok, "error": db_error},
            "scheduler": {"ok": sched_ok, "job_count": scheduler_job_count()},
            "bot": {"ok": runtime["bot_started"]},
        },
        "runtime": runtime,
    }
    return payload, bool(db_ok and sched_ok and runtime["bot_started"])


def build_metrics_payload(runtime_state: RuntimeState, scheduler_job_count: Callable[[], int]) -> dict:
    runtime = runtime_state.as_dict()
    return {
        "metrics": {
            **runtime,
            "scheduler_job_count": scheduler_job_count(),
        }
    }
