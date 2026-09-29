"""Conversation state and Telegram update handlers."""

from __future__ import annotations

import difflib
import html
from dataclasses import dataclass, field

from .catalog import CATEGORY_NAMES, Catalog, Recipe, SOURCE, SOURCE_URL, LICENSE_URL


PAGE_SIZE = 10
MAX_TEXT = 3500
UNITS = {
    "g": "г", "kg": "кг", "ml": "мл", "l": "л", "piece": "шт.",
    "tbsp": "ст. л.", "tsp": "ч. л.", "clove": "зубч.",
    "pinch": "щеп.", "sprig": "веточ.", "slice": "ломт.",
}


def safe(value: object) -> str:
    return html.escape(str(value), quote=True)


def keyboard(rows: list[list[tuple[str, str]]]) -> dict:
    return {"inline_keyboard": [[{"text": label, "callback_data": data} for label, data in row]
                                for row in rows]}


@dataclass
class Session:
    selected: set[str] = field(default_factory=set)
    results: list[Recipe] = field(default_factory=list)
    category: str | None = None


class Bot:
    def __init__(self, catalog: Catalog, api):
        self.catalog = catalog
        self.api = api
        self.sessions: dict[tuple[int, int], Session] = {}

    def _session(self, chat_id: int, user_id: int) -> Session:
        return self.sessions.setdefault((chat_id, user_id), Session())

    def _ingredients_keyboard(self, session: Session, page: int = 0) -> dict:
        ids = self.catalog.ingredient_ids
        last_page = max(0, (len(ids) - 1) // PAGE_SIZE)
        page = max(0, min(page, last_page))
        start = page * PAGE_SIZE
        rows = []
        for index in range(start, min(start + PAGE_SIZE, len(ids))):
            item_id = ids[index]
            mark = "✓ " if item_id in session.selected else ""
            rows.append([(mark + self.catalog.ingredients[item_id], f"i:{index}:{page}")])
        navigation = []
        if page:
            navigation.append(("◀", f"p:{page - 1}"))
        navigation.append((f"{page + 1}/{last_page + 1}", "noop"))
        if page < last_page:
            navigation.append(("▶", f"p:{page + 1}"))
        rows.append(navigation)
        rows.append([("Очистить", "clear"), ("Готово", "done")])
        rows.append([("🏠 Домой", "home")])
        return keyboard(rows)

    def _categories_keyboard(self) -> dict:
        rows = []
        for index, category in enumerate(self.catalog.categories):
            rows.append([(CATEGORY_NAMES.get(category, category.capitalize()), f"c:{index}")])
        rows.append([("⬅ Назад", "b:ingredients"), ("🏠 Домой", "home")])
        return keyboard(rows)

    def _start(self, chat_id: int, user_id: int) -> None:
        self.sessions[(chat_id, user_id)] = session = Session()
        self.api.send(chat_id,
            "Привет! Выберите продукты кнопками или напишите их названия через запятую. "
            "Специи, вода и масло считаются доступными. Когда закончите, нажмите «Готово».\n"
            "Количество продуктов пока не учитывается. Если чего-то не хватает, "
            "покажу ближайшие рецепты и недостающие продукты.", self._ingredients_keyboard(session))

    def _show_categories(self, chat_id: int, session: Session) -> None:
        chosen = ", ".join(safe(self.catalog.ingredients[x]) for x in sorted(session.selected))
        self.api.send(chat_id, "У вас есть: " + chosen + "\nКакое блюдо хотите?",
                      self._categories_keyboard())

    def _results_keyboard(self, recipes: list[Recipe]) -> dict:
        rows = [[(recipe.name, f"r:{recipe.slug}")] for recipe in recipes]
        rows.append([("⬅ Назад", "b:categories"), ("🏠 Домой", "home")])
        return keyboard(rows)

    def _show_results(self, chat_id: int, session: Session, category: str) -> None:
        session.category = category
        session.results = self.catalog.find(session.selected, category)[:5]
        if session.results:
            lines = ["Подходящие блюда:"]
            for i, recipe in enumerate(session.results, 1):
                minutes = recipe.prep_minutes + recipe.cook_minutes
                lines.append(f"{i}. {safe(recipe.name)} — {minutes} мин")
            self.api.send(chat_id, "\n".join(lines) + "\nНажмите на блюдо, чтобы увидеть рецепт.",
                          self._results_keyboard(session.results))
            return
        suggestions = self.catalog.suggest(session.selected, category)[:5]
        if suggestions:
            session.results = [recipe for recipe, _ in suggestions]
            chosen = ", ".join(safe(self.catalog.ingredients[key]) for key in sorted(session.selected))
            lines = ["Блюд только из выбранных продуктов не нашлось.",
                     f"С учётом уже имеющихся: {chosen}.",
                     "Вот ближайшие рецепты; для них также понадобятся:"]
            for i, (recipe, missing) in enumerate(suggestions, 1):
                names = [safe(self.catalog.ingredients[key]) for key in missing[:5]]
                more = f" и ещё {len(missing) - 5}" if len(missing) > 5 else ""
                lines.append(f"{i}. {safe(recipe.name)} — " + ", ".join(names) + more)
            self.api.send(chat_id, "\n".join(lines), self._results_keyboard(session.results))
            return
        self.api.send(chat_id, "Блюд с выбранными продуктами в этой категории "
                      "не нашлось. Добавьте продукты или выберите другую категорию.",
                      self._categories_keyboard())

    def handle(self, update: dict) -> None:
        if "message" in update:
            message = update["message"]
            chat_id = message["chat"]["id"]
            user_id = message.get("from", {}).get("id", chat_id)
            text = message.get("text", "").strip()
            session = self._session(chat_id, user_id)
            if text.startswith("/start") or text.startswith("/reset"):
                self._start(chat_id, user_id)
            elif text.startswith("/"):
                self.api.send(chat_id, "Неизвестная команда. Нажмите /start, чтобы начать подбор.")
            elif not text:
                self.api.send(chat_id, "Напишите названия продуктов через запятую или нажмите /start.")
            else:
                self._add_text_ingredients(chat_id, session, text)
        elif "callback_query" in update:
            self._callback(update["callback_query"])

    def _add_text_ingredients(self, chat_id: int, session: Session, text: str) -> None:
        terms = [term.strip() for term in text.split(",") if term.strip()]
        added, unknown = [], []
        for term in terms[:30]:
            item_id = self.catalog.resolve_ingredient(term)
            if item_id:
                session.selected.add(item_id)
                added.append(self.catalog.ingredients[item_id])
            else:
                suggestions = difflib.get_close_matches(
                    term.casefold(), [name.casefold() for name in self.catalog.ingredients.values()], n=2)
                unknown.append(term + (" (возможно: " + ", ".join(suggestions) + ")" if suggestions else ""))
        if not terms:
            self.api.send(chat_id, "Укажите хотя бы один продукт. Например: яйцо, помидор.")
            return
        lines = []
        if added:
            lines.append("Добавлено: " + ", ".join(safe(x) for x in added))
        if unknown:
            lines.append("Не нашёл в каталоге: " + ", ".join(safe(x) for x in unknown))
        if len(terms) > 30:
            lines.append("За раз можно указать не более 30 продуктов.")
        lines.append("Можно добавить ещё продукты или нажать «Готово».")
        self.api.send(chat_id, "\n".join(lines), self._ingredients_keyboard(session))

    def _callback(self, callback: dict) -> None:
        callback_id = callback["id"]
        message = callback.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        user_id = callback.get("from", {}).get("id")
        if chat_id is None or user_id is None:
            self.api.answer_callback(callback_id, "Не удалось определить чат. Начните с /start.")
            return
        session = self._session(chat_id, user_id)
        parts = callback.get("data", "").split(":")
        action = parts[0]
        try:
            if action == "home":
                self._start(chat_id, user_id)
            elif action == "b" and len(parts) >= 2:
                if parts[1] == "ingredients" and len(parts) == 2:
                    self.api.send(chat_id, "Выберите продукты или нажмите «Готово».",
                                  self._ingredients_keyboard(session))
                elif parts[1] == "categories" and len(parts) == 2 and session.selected:
                    self._show_categories(chat_id, session)
                elif (parts[1] == "results" and len(parts) == 3 and session.selected
                      and parts[2] in self.catalog.categories):
                    self._show_results(chat_id, session, parts[2])
                else:
                    self.api.send(chat_id, "Кнопка устарела. Нажмите /start и повторите выбор.")
            elif action == "i" and len(parts) == 3:
                index, page = int(parts[1]), int(parts[2])
                item_id = self.catalog.ingredient_ids[index]
                if item_id in session.selected:
                    session.selected.remove(item_id)
                else:
                    session.selected.add(item_id)
                self.api.edit_keyboard(chat_id, message["message_id"], self._ingredients_keyboard(session, page))
            elif action == "p" and len(parts) == 2:
                self.api.edit_keyboard(chat_id, message["message_id"],
                                       self._ingredients_keyboard(session, int(parts[1])))
            elif action == "clear":
                session.selected.clear()
                session.results.clear()
                session.category = None
                self.api.send(chat_id, "Список очищен. Выберите продукты заново.",
                              self._ingredients_keyboard(session))
            elif action == "done":
                if not session.selected:
                    self.api.send(chat_id, "Сначала выберите хотя бы один продукт.")
                else:
                    self._show_categories(chat_id, session)
            elif action == "c" and len(parts) == 2:
                category = self.catalog.categories[int(parts[1])]
                if not session.selected:
                    self.api.send(chat_id, "Сначала выберите продукты через /start.")
                else:
                    self._show_results(chat_id, session, category)
            elif action == "r" and len(parts) == 2:
                recipe = self.catalog.recipes_by_slug[parts[1]]
                chunks = recipe_messages(recipe)
                for chunk in chunks[:-1]:
                    self.api.send(chat_id, chunk)
                back = f"b:results:{recipe.category}" if session.selected else "b:ingredients"
                self.api.send(chat_id, chunks[-1], keyboard([
                    [("⬅ Назад", back), ("🏠 Домой", "home")]
                ]))
            elif action != "noop":
                self.api.send(chat_id, "Кнопка устарела. Нажмите /start и повторите выбор.")
        except (ValueError, IndexError, KeyError):
            self.api.send(chat_id, "Не удалось обработать выбор. Нажмите /start и попробуйте снова.")
        finally:
            self.api.answer_callback(callback_id)


def recipe_messages(recipe: Recipe) -> list[str]:
    total = recipe.prep_minutes + recipe.cook_minutes
    header = [f"<b>{safe(recipe.name)}</b>", safe(recipe.summary),
              f"{safe(CATEGORY_NAMES.get(recipe.category, recipe.category))} · {total} мин · "
              f"{recipe.servings} порц.", "", "<b>Ингредиенты:</b>"]
    for item in recipe.ingredients:
        amount = item.get("quantity")
        unit = item.get("unit", "")
        quantity = "по вкусу" if amount is None or unit == "toTaste" else f"{amount} {safe(UNITS.get(unit, unit))}"
        note = item.get("note")
        detail = (" (" + safe(note["ru"]) + ")") if isinstance(note, dict) and note.get("ru") else ""
        header.append(f"• {safe(item['name']['ru'])} — {quantity}{detail}")
    header.extend(["", "<b>Приготовление:</b>"])
    chunks = []
    current = "\n".join(header)
    for index, step in enumerate(recipe.steps, 1):
        duration = f" ({step['minutes']} мин)" if step["minutes"] is not None else ""
        line = f"{index}. {safe(step['text']['ru'])}{duration}"
        if len(current) + len(line) + 1 > MAX_TEXT:
            chunks.append(current)
            current = "<b>Продолжение:</b>"
        current += "\n" + line
    attribution = (f"\n\nИсточник: <a href=\"{SOURCE_URL}\">{safe(SOURCE)}</a>, "
                   f"<a href=\"{LICENSE_URL}\">CC BY-SA 4.0</a>.")
    if len(current) + len(attribution) > MAX_TEXT:
        chunks.append(current)
        current = ""
    chunks.append(current + attribution)
    return chunks
