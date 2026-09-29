"""Run the bot using Telegram long polling."""

from __future__ import annotations

import logging
import os
import sys
import time

from .bot import Bot
from .catalog import Catalog
from .telegram import TelegramAPI


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token = os.getenv("BOT_TOKEN", "")
    path = os.getenv("RECIPES_FILE", "data/unitools-recipes-v1.json")
    try:
        catalog = Catalog.from_file(path)
        api = TelegramAPI(token)
    except (OSError, ValueError) as exc:
        logging.error("Не удалось запустить бота: %s", exc)
        return 2
    bot = Bot(catalog, api)
    offset = None
    logging.info("Бот запущен, рецептов: %d", len(catalog.recipes))
    while True:
        try:
            params = {"timeout": 25, "allowed_updates": ["message", "callback_query"]}
            if offset is not None:
                params["offset"] = offset
            updates = api.call("getUpdates", **params)
            for update in updates:
                offset = update["update_id"] + 1
                try:
                    bot.handle(update)
                except Exception:
                    logging.exception("Ошибка обработки обновления %s", update.get("update_id"))
        except KeyboardInterrupt:
            logging.info("Бот остановлен")
            return 0
        except Exception as exc:
            logging.error("Ошибка опроса Telegram: %s", exc)
            time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
