from __future__ import annotations

import json

from app.models import EvalCase


class EvalBuilder:
    def add_candidate(
        self,
        session,
        *,
        label: str,
        input_text: str,
        expected_action: str,
        expected_json: dict | None = None,
    ) -> EvalCase:
        row = EvalCase(
            label=label,
            input_text=input_text,
            expected_action=expected_action,
            expected_json=json.dumps(expected_json or {}, ensure_ascii=False),
            active=True,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row
