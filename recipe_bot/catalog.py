"""Load and search the UniTools World Recipes dataset."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


SOURCE = "UniTools — theunitools.com"
SOURCE_URL = "https://theunitools.com/ru/data"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"

CATEGORY_NAMES = {
    "breakfast": "Завтрак",
    "soup": "Первое (суп)",
    "main": "Второе (основное)",
    "side": "Гарнир",
    "salad": "Салат",
    "snack": "Перекус",
    "dessert": "Десерт",
    "drink": "Напиток",
    "bread": "Выпечка и хлеб",
    "sauce": "Соус",
}

# Pantry items chosen by the user. Exotic fresh roots and herbs are not silently
# treated as spices; the set can be extended after inspecting the full dataset.
PANTRY_WORDS = {
    "соль", "перец", "паприка", "куркума", "корица", "зира", "кумин",
    "кориандр", "кардамон", "гвоздика", "шафран", "орегано", "тимьян",
    "тмин", "мускатный", "карри", "приправа", "специи", "лавровый",
    "вода", "масло", "чили", "бибер", "бадьян", "аджван", "пажитник",
}

NOT_PANTRY_IDS = {
    "chicken", "currysauce", "currypaste", "curryleaves", "vegetables",
    "orangeblossom", "rosewater", "saltpetre", "greenpepper", "redpepper",
    "sweetpepper",
}


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold().replace("ё", "е").strip())


def is_pantry(ingredient: dict) -> bool:
    if ingredient.get("id") in NOT_PANTRY_IDS:
        return False
    name = normalize(ingredient.get("name", {}).get("ru", ""))
    if ingredient.get("id") in {"pepper", "peppers"} and name in {
        "перец сладкий", "сладкий перец", "красный перец",
    }:
        return False
    words = set(re.findall(r"[а-яa-z]+", name))
    # "Вода или бульон" is usable with water; broth alone is not pantry.
    return bool(words & PANTRY_WORDS)


def ingredient_key(ingredient: dict) -> str:
    """Unify common naming variants while retaining distinct products."""
    name = normalize(ingredient["name"]["ru"])
    if re.match(r"^яйц[оаы](?:\b|\s)", name) and "порош" not in name:
        return "яйцо"
    if name in {"помидор", "помидоры", "спелые помидоры", "помидоры крупные"}:
        return "помидоры"
    if name in {"лук", "мелкий лук", "лук крупный", "лук для подачи"}:
        return "лук"
    return name


@dataclass(frozen=True)
class Recipe:
    slug: str
    name: str
    summary: str
    category: str
    country: str
    difficulty: str
    servings: int
    prep_minutes: int
    cook_minutes: int
    nutrition: dict
    ingredients: tuple[dict, ...]
    steps: tuple[dict, ...]

    @property
    def required_keys(self) -> frozenset[str]:
        return frozenset(ingredient_key(item) for item in self.ingredients
                         if not is_pantry(item))


class Catalog:
    def __init__(self, recipes: list[Recipe]):
        if not recipes:
            raise ValueError("В каталоге нет рецептов")
        self.recipes = recipes
        self.recipes_by_slug = {recipe.slug: recipe for recipe in recipes}
        if len(self.recipes_by_slug) != len(recipes):
            raise ValueError("В каталоге повторяются slug рецептов")
        self.ingredients: dict[str, str] = {}
        self.frequency: dict[str, int] = {}
        self.id_to_key: dict[str, str] = {}
        for recipe in recipes:
            for item in recipe.ingredients:
                if is_pantry(item):
                    continue
                key = ingredient_key(item)
                self.id_to_key[item["id"]] = key
                self.ingredients[key] = item["name"]["ru"]
                self.frequency[key] = self.frequency.get(key, 0) + 1
        self.ingredient_ids = sorted(
            self.ingredients, key=lambda key: (-self.frequency[key], self.ingredients[key].casefold())
        )
        self.categories = sorted({recipe.category for recipe in recipes})

    @classmethod
    def from_file(cls, path: str | Path) -> Catalog:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Корень файла рецептов должен быть объектом")
        if data.get("version") != "2.0.0" or data.get("license") != "CC BY-SA 4.0":
            raise ValueError("Ожидался набор UniTools версии 2.0.0 с лицензией CC BY-SA 4.0")
        if not isinstance(data.get("recipes"), list) or not data["recipes"]:
            raise ValueError("В файле нет списка рецептов")
        recipes = []
        for row in data["recipes"]:
            if not isinstance(row, dict):
                raise ValueError("Запись рецепта должна быть объектом")
            try:
                if not isinstance(row["ingredients"], list) or not isinstance(row["steps"], list):
                    raise ValueError("Ингредиенты и шаги должны быть списками")
                for item in row["ingredients"]:
                    if (not isinstance(item, dict) or not isinstance(item["id"], str)
                            or not isinstance(item["name"]["ru"], str)):
                        raise ValueError("Неверный ингредиент")
                for step in row["steps"]:
                    if (not isinstance(step, dict) or not isinstance(step["text"]["ru"], str)
                            or (step["minutes"] is not None
                                and not isinstance(step["minutes"], (int, float)))):
                        raise ValueError("Неверный шаг приготовления")
                recipes.append(Recipe(
                    slug=row["slug"], name=row["name"]["ru"],
                    summary=row["summary"]["ru"], category=row["category"],
                    country=row["country"], difficulty=row["difficulty"],
                    servings=row["baseServings"], prep_minutes=row["prepMinutes"],
                    cook_minutes=row["cookMinutes"], nutrition=row["nutritionPerServing"],
                    ingredients=tuple(row["ingredients"]), steps=tuple(row["steps"]),
                ))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"Неполная запись рецепта {row.get('slug', '?')}: {exc}") from exc
        return cls(recipes)

    def resolve_ingredient(self, query: str) -> str | None:
        key = normalize(query)
        if key in self.ingredients:
            return key
        alias = ingredient_key({"name": {"ru": query}})
        if alias in self.ingredients:
            return alias
        by_id = [ingredient_key for item_id, ingredient_key in self.id_to_key.items()
                 if normalize(item_id.replace("_", "-")) == key]
        return by_id[0] if len(by_id) == 1 else None

    def find(self, selected: set[str], category: str) -> list[Recipe]:
        if category not in self.categories:
            return []
        available = set(selected)
        if "яйцо" in available:
            available.update({"яичные желтки", "яичные белки", "яичный желток", "яичный белок"})
        matches = [r for r in self.recipes if r.category == category and r.required_keys <= available]
        return sorted(matches, key=lambda r: (len(r.required_keys), r.name.casefold()))
