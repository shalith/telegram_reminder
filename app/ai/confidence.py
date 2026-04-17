from __future__ import annotations


def compute_final_confidence(*, model_confidence: float, checker_penalty: float = 0.0, parser_agreement_bonus: float = 0.0, target_penalty: float = 0.0, duplicate_penalty: float = 0.0) -> float:
    value = model_confidence + parser_agreement_bonus - checker_penalty - target_penalty - duplicate_penalty
    return max(0.0, min(1.0, round(value, 3)))


def should_auto_execute(confidence: float, threshold: float) -> bool:
    return confidence >= threshold
