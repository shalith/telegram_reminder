from app.ai.schemas import InterpretationEnvelope, get_interpretation_schema


def test_schema_builds():
    schema = get_interpretation_schema()
    assert schema["title"] == "InterpretationEnvelope"
    env = InterpretationEnvelope()
    assert env.action == "clarify"
