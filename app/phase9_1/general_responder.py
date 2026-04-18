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
            return (
                'I can help with reminders, wake-up alerts, schedules, edits, and daily agenda. '
                'Try something like: “Wake me up tomorrow at 7 AM” or “Remind me to call John at 5 PM.”'
            )
        try:
            response = self.client.chat.completions.create(
                model=self.settings.groq_model,
                temperature=0.3,
                messages=[
                    {
                        'role': 'system',
                        'content': (
                            'You are a brief Telegram personal assistant. '
                            'Respond naturally and helpfully. '
                            'Do not schedule reminders directly here; only answer the general message.'
                        ),
                    },
                    {'role': 'user', 'content': message_text},
                ],
            )
            text = (response.choices[0].message.content or '').strip()
            return text or 'How can I help?'
        except Exception:
            return 'How can I help?'
