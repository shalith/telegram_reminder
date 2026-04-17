from app.ai.interpreter import StructuredInterpreter
from app.ai.schemas import PendingConversationState
from app.config import Settings


def test_interpreter_falls_back_without_groq():
    settings = Settings(telegram_bot_token="x")
    interpreter = StructuredInterpreter(settings)
    result = interpreter.interpret(message_text="show my reminders", timezone_name="Asia/Singapore", pending_state=None, open_reminders=[], preference_snapshot="none")
    assert result.envelope.action == "list_reminders"
