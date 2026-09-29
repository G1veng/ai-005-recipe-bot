"""Fetch and validate the current UniTools JSON or import a downloaded copy."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from recipe_bot.catalog import Catalog

URL = "https://theunitools.com/data/unitools-recipes-v1.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="existing JSON file when direct download is unavailable")
    parser.add_argument("--output", type=Path, default=Path("data/unitools-recipes-v1.json"))
    args = parser.parse_args()
    try:
        if args.source:
            raw = args.source.read_bytes()
        else:
            request = urllib.request.Request(URL, headers={"User-Agent": "AI-005-recipe-bot/1.0"})
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read(8_000_001)
            if len(raw) > 8_000_000:
                raise ValueError("Файл данных превышает лимит 8 МБ")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("Корень файла рецептов должен быть объектом")
        if data.get("version") != "2.0.0" or data.get("license") != "CC BY-SA 4.0":
            raise ValueError("Неверная версия или лицензия UniTools")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(".tmp")
        temporary.write_bytes(raw)
        try:
            Catalog.from_file(temporary)
            temporary.replace(args.output)
        finally:
            temporary.unlink(missing_ok=True)
        print(f"Рецептов: {len(data['recipes'])}; SHA-256: {hashlib.sha256(raw).hexdigest()}")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Не удалось получить данные UniTools: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
