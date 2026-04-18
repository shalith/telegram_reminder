from __future__ import annotations

from dataclasses import dataclass

from app.learning.correction_memory import SimilarCorrection


@dataclass(slots=True)
class ConfidenceAdjustment:
    adjusted_confidence: float
    reasons: list[str]


def adapt_confidence(*, base_confidence: float, positive_examples: list[SimilarCorrection], risky_examples: list[SimilarCorrection]) -> ConfidenceAdjustment:
    adjusted = base_confidence
    reasons: list[str] = []
    if positive_examples:
        boost = min(0.12, 0.03 * len(positive_examples))
        adjusted += boost
        reasons.append("similar_confirmed_example")
    if risky_examples:
        penalty = min(0.25, 0.08 * len(risky_examples))
        adjusted -= penalty
        reasons.append("similar_corrected_example")
    adjusted = max(0.0, min(1.0, adjusted))
    return ConfidenceAdjustment(adjusted_confidence=adjusted, reasons=reasons)
