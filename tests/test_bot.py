import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from recipe_bot.bot import Bot, recipe_messages
from recipe_bot.catalog import Catalog


FIXTURE = Path(__file__).parent / "fixtures" / "recipes.json"


class FakeAPI:
    def __init__(self):
        self.sent = []
        self.edits = []
        self.answers = []

    def send(self, chat_id, text, keyboard=None):
        self.sent.append((chat_id, text, keyboard))

    def edit_keyboard(self, chat_id, message_id, keyboard):
        self.edits.append((chat_id, message_id, keyboard))

    def answer_callback(self, callback_id, text=""):
        self.answers.append((callback_id, text))


def message(text):
    return {"message": {"chat": {"id": 42}, "from": {"id": 7}, "text": text}}


def callback(data):
    return {"callback_query": {"id": "cb1", "from": {"id": 7},
            "message": {"chat": {"id": 42}, "message_id": 99}, "data": data}}


class BotFlowTests(unittest.TestCase):
    def setUp(self):
        self.catalog = Catalog.from_file(FIXTURE)
        self.api = FakeAPI()
        self.bot = Bot(self.catalog, self.api)

    def test_start_selection_category_and_full_recipe(self):
        self.bot.handle(message("/start"))
        self.assertIn("Выберите продукты", self.api.sent[-1][1])
        self.assertIsNotNone(self.api.sent[-1][2])
        self.bot.handle(message("Яйцо, Помидор"))
        self.assertIn("Добавлено", self.api.sent[-1][1])
        self.bot.handle(callback("done"))
        self.assertIn("Какое блюдо", self.api.sent[-1][1])
        category_index = self.catalog.categories.index("breakfast")
        self.bot.handle(callback(f"c:{category_index}"))
        self.assertIn("Тестовый омлет", self.api.sent[-1][1])
        self.bot.handle(callback("r:test-omelet"))
        self.assertIn("Ингредиенты", self.api.sent[-1][1])
        self.assertIn("Приготовление", self.api.sent[-1][1])
        self.assertIn("нарезать кубиками", self.api.sent[-1][1])
        self.assertIn("шт.", self.api.sent[-1][1])
        self.assertIn("CC BY-SA 4.0", self.api.sent[-1][1])
        self.assertEqual(len(self.api.answers), 3)

    def test_button_selection_and_no_match(self):
        self.bot.handle(message("/start"))
        index = self.catalog.ingredient_ids.index("яйцо")
        self.bot.handle(callback(f"i:{index}:0"))
        self.assertEqual(len(self.api.edits), 1)
        self.bot.handle(callback("done"))
        category_index = self.catalog.categories.index("main")
        self.bot.handle(callback(f"c:{category_index}"))
        self.assertIn("не нашлось", self.api.sent[-1][1])

    def test_partial_match_shows_missing_products_and_recipe(self):
        self.bot.handle(message("/start"))
        self.bot.handle(message("Помидор"))
        self.bot.handle(callback("done"))
        category_index = self.catalog.categories.index("breakfast")
        self.bot.handle(callback(f"c:{category_index}"))
        self.assertIn("ближайшие рецепты", self.api.sent[-1][1])
        self.assertIn("Жирным — уже есть", self.api.sent[-1][1])
        self.assertNotIn("С учётом уже имеющихся", self.api.sent[-1][1])
        line = self.api.sent[-1][1].splitlines()[-1]
        self.assertIn("Тестовый омлет — <b>Помидор</b>", line)
        self.assertIn("<b>Растительное масло</b>", line)
        self.assertIn("<b>Соль</b>", line)
        self.assertTrue(line.endswith(", Яйцо"))
        button = self.api.sent[-1][2]["inline_keyboard"][0][0]["callback_data"]
        self.bot.handle(callback(button))
        self.assertIn("Приготовление", self.api.sent[-1][1])

    def test_sort_by_missing_count_then_alphabet(self):
        pasta = self.catalog.recipes_by_slug["test-pasta"]
        egg = self.catalog.recipes_by_slug["test-omelet"].ingredients[0]
        same = replace(pasta, slug="same", name="Бета")
        more = replace(pasta, slug="more", name="Альфа",
                       ingredients=pasta.ingredients + (egg,))
        catalog = Catalog([pasta, same, more])
        self.assertEqual([r.slug for r, _ in catalog.suggest({"помидоры"}, "main")],
                         ["same", "test-pasta", "more"])
        self.assertEqual([r.slug for r in catalog.find({"помидоры", "макароны", "яйцо"}, "main")],
                         ["more", "same", "test-pasta"])

    def test_back_preserves_selection_and_home_resets(self):
        self.bot.handle(message("/start"))
        self.bot.handle(message("Помидор"))
        self.bot.handle(callback("done"))
        self.bot.handle(callback("b:ingredients"))
        self.assertIn("Выберите продукты", self.api.sent[-1][1])
        self.assertEqual(self.bot._session(42, 7).selected, {"помидоры"})
        self.bot.handle(callback("done"))
        self.bot.handle(callback(f"c:{self.catalog.categories.index('breakfast')}"))
        self.bot.handle(callback("r:test-omelet"))
        self.assertEqual(self.api.sent[-1][2]["inline_keyboard"][0][0]["callback_data"],
                         "b:results:breakfast")
        self.bot.handle(callback("b:results:breakfast"))
        self.assertIn("ближайшие рецепты", self.api.sent[-1][1])
        self.bot.handle(callback("b:categories"))
        self.assertIn("Какое блюдо", self.api.sent[-1][1])
        self.bot.handle(callback("home"))
        self.assertIn("Привет!", self.api.sent[-1][1])
        self.assertEqual(self.bot._session(42, 7).selected, set())
        self.assertIsNone(self.bot._session(42, 7).category)

    def test_bad_input_and_callbacks(self):
        self.bot.handle(message("/unknown"))
        self.assertIn("Неизвестная команда", self.api.sent[-1][1])
        self.bot.handle(message("Несуществующий продукт"))
        self.assertIn("Не нашёл", self.api.sent[-1][1])
        self.bot.handle(callback("done"))
        self.assertIn("хотя бы один", self.api.sent[-1][1])
        self.bot.handle(callback("i:9999:0"))
        self.assertIn("Не удалось обработать", self.api.sent[-1][1])
        self.bot.handle(callback("not-valid"))
        self.assertIn("устарела", self.api.sent[-1][1])

    def test_catalog_validation_and_pantry(self):
        self.assertEqual([r.slug for r in self.catalog.find({"помидоры"}, "soup")], ["test-soup"])
        self.assertEqual(self.catalog.find({"яйцо"}, "breakfast"), [])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            data = json.loads(FIXTURE.read_text())
            data["version"] = "3.0.0"
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                Catalog.from_file(path)

    def test_same_product_names_are_merged(self):
        data = json.loads(FIXTURE.read_text())
        data["recipes"][0]["ingredients"][0]["name"]["ru"] = "Яйца"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recipes.json"
            path.write_text(json.dumps(data, ensure_ascii=False))
            catalog = Catalog.from_file(path)
        self.assertEqual(catalog.resolve_ingredient("Яйцо"), "яйцо")
        self.assertEqual([r.slug for r in catalog.find({"яйцо", "помидоры"}, "breakfast")],
                         ["test-omelet"])

    def test_old_result_button_keeps_recipe_identity(self):
        self.bot.handle(message("/start"))
        self.bot.handle(message("Яйцо, Помидор, Макароны"))
        self.bot.handle(callback("done"))
        self.bot.handle(callback(f"c:{self.catalog.categories.index('breakfast')}"))
        old_button = self.api.sent[-1][2]["inline_keyboard"][0][0]["callback_data"]
        self.bot.handle(callback(f"c:{self.catalog.categories.index('main')}"))
        self.bot.handle(callback(old_button))
        self.assertIn("Тестовый омлет", self.api.sent[-1][1])
        self.assertNotIn("Тестовая паста", self.api.sent[-1][1])
        back_button = self.api.sent[-1][2]["inline_keyboard"][0][0]["callback_data"]
        self.bot.handle(callback(back_button))
        self.assertIn("Тестовый омлет", self.api.sent[-1][1])
        self.assertNotIn("Тестовая паста", self.api.sent[-1][1])
        self.bot.handle(callback(old_button))
        back_button = self.api.sent[-1][2]["inline_keyboard"][0][0]["callback_data"]
        self.bot.handle(callback(f"c:{self.catalog.categories.index('main')}"))
        self.bot.handle(callback(back_button))
        self.assertIn("Тестовый омлет", self.api.sent[-1][1])
        self.assertNotIn("Тестовая паста", self.api.sent[-1][1])

    def test_nested_corrupt_data_is_rejected(self):
        data = json.loads(FIXTURE.read_text())
        del data["recipes"][0]["ingredients"][0]["name"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(json.dumps(data, ensure_ascii=False))
            with self.assertRaisesRegex(ValueError, "Неполная запись"):
                Catalog.from_file(path)

    def test_long_recipe_is_split(self):
        recipe = self.catalog.recipes[0]
        from dataclasses import replace
        long = replace(recipe, steps=tuple({"text": {"ru": "Текст " * 120}, "minutes": 1}
                                          for _ in range(8)))
        chunks = recipe_messages(long)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 3500 for chunk in chunks))
        self.assertIn("theunitools.com", chunks[-1])


if __name__ == "__main__":
    unittest.main()
