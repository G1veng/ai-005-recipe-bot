"""Small Telegram Bot API client built on the Python standard library."""

from __future__ import annotations

import json
import urllib.error
import urllib.request


class TelegramAPI:
    def __init__(self, token: str):
        if not token or any(character.isspace() for character in token):
            raise ValueError("BOT_TOKEN не задан или содержит пробелы")
        self.base_url = f"https://api.telegram.org/bot{token}/"

    def call(self, method: str, **params):
        body = json.dumps(params, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + method, data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=40) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            # Telegram may put the token in request URLs; never log the URL/body.
            raise RuntimeError(f"Telegram HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Telegram network error: {exc.reason}") from None
        if not result.get("ok"):
            raise RuntimeError(f"Telegram API error: {result.get('error_code', 'unknown')}")
        return result["result"]

    def send(self, chat_id: int, text: str, keyboard: dict | None = None):
        params = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if keyboard:
            params["reply_markup"] = keyboard
        return self.call("sendMessage", **params)

    def answer_callback(self, callback_id: str, text: str = ""):
        return self.call("answerCallbackQuery", callback_query_id=callback_id, text=text)

    def edit_keyboard(self, chat_id: int, message_id: int, keyboard: dict):
        return self.call("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id,
                         reply_markup=keyboard)
