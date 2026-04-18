from __future__ import annotations

from app.ai.interpreter import Groq
from app.config import Settings


class GeneralResponder:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = Groq(api_key=settings.groq_api_key) if settings.groq_enabled and Groq is not None else None

    def respond(self, *, message_text: str) -> str:
        lowered = (message_text or '').strip().lower()
        canned = {
            'hello': 'Hello — how can I help?',
            'hi': 'Hi — how can I help?',
            'hey': 'Hello — how can I help?',
            'thanks': "You're welcome.",
            'thank you': "You're welcome.",
            'ok': 'Okay.',
            'okay': 'Okay.',
        }
        if lowered in canned:
            return canned[lowered]
        if self.client is None:
            return ('I can help with reminders, wake-up alerts, daily agenda, updates, deletes, and quick questions. ' 'Try something like: “Wake me up tomorrow at 7 AM”, “List tomorrow reminders”, or “Update reminder 2 to 5 PM.”')
        try:
            response = self.client.chat.completions.create(
                model=self.settings.groq_model,
                temperature=0.3,
                messages=[
                    {
                        'role': 'system',
                        'content': (
                            'You are a brief Telegram personal assistant for a reminder bot. '
                            'Respond naturally and helpfully, but stay consistent with the bot capabilities. '
                            'This bot CAN create, list, update, delete, and summarize reminders and agenda from its own reminder database. '
                            'Never say you do not have access to reminders, agenda, or schedules if the user is asking about this bot\'s stored reminders. '
                            'If the user asks to list reminders or agenda, the caller should use tools instead of this responder. '
                            'Do not schedule reminders directly here; only answer true general messages.'
                        ),
                    },
                    {'role': 'user', 'content': message_text},
                ],
            )
            text = (response.choices[0].message.content or '').strip()
            return text or 'How can I help?'
        except Exception:
            return 'How can I help?'
