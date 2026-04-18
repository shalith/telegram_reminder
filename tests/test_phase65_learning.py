from app.learning.confidence_adapter import adapt_confidence
from app.learning.self_learning import SelfLearningEngine
from app.models import LearnedTimePattern, PhraseRiskScore


class DummyExample:
    def __init__(self, notes: str | None = None):
        self.source_text = "i want to go for gym today 2am"
        self.action_name = "create_reminder"
        self.resolved_task = "go for gym"
        self.resolved_time_phrase = "today 2 PM"
        self.notes = notes


class DummySimilar:
    def __init__(self, notes: str | None = None):
        self.example = DummyExample(notes=notes)


def test_confidence_adapter_boosts_and_penalizes():
    result = adapt_confidence(base_confidence=0.6, positive_examples=[DummySimilar('confirmed')], risky_examples=[DummySimilar('corrected')])
    assert 0 <= result.adjusted_confidence <= 1
    assert 'similar_confirmed_example' in result.reasons
    assert 'similar_corrected_example' in result.reasons


def test_self_learning_signature_masks_times():
    engine = SelfLearningEngine()
    signature = engine.build_signature('Remind me to go for gym today 2am')
    assert '<time>' in signature or '<n>' in signature


def test_record_risk_and_lookup(session):
    engine = SelfLearningEngine()
    signature = engine.build_signature('I want to go for gym today 2am')
    engine.record_correction(session, telegram_user_id=123, signature=signature, notes='corrected')
    assert engine.lookup_risk_score(session, telegram_user_id=123, signature=signature) > 0


def test_apply_learned_time_patterns(session):
    engine = SelfLearningEngine()
    row = LearnedTimePattern(raw_phrase='today morning 9', normalized_phrase='today at 9 AM', success_count=3)
    session.add(row)
    session.commit()
    prepared, applied = engine._apply_learned_time_patterns(session, 'Remind me to pay rent today morning 9')
    assert 'today at 9 AM' in prepared
    assert applied
