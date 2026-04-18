from app.config import Settings
from app.phase9_1.router import LLMConversationRouter


def make_settings() -> Settings:
    return Settings(telegram_bot_token='x', groq_api_key=None)


def test_router_general_chat():
    router = LLMConversationRouter(make_settings())
    decision = router.route(message_text='Hello there', has_active_thread=False, has_pending_confirmation=False, has_pending_follow_up=False)
    assert decision.route == 'general_chat'


def test_router_reminder_conversation():
    router = LLMConversationRouter(make_settings())
    decision = router.route(message_text='Wake me up tomorrow at 7 AM', has_active_thread=False, has_pending_confirmation=False, has_pending_follow_up=False)
    assert decision.route == 'reminder_conversation'


def test_router_confirmation_reply():
    router = LLMConversationRouter(make_settings())
    decision = router.route(message_text='ok', has_active_thread=True, has_pending_confirmation=True, has_pending_follow_up=False)
    assert decision.route == 'confirmation_reply'
