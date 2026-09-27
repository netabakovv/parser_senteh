from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .checker import check_cart
from .collector import SECTIONS, collect
from .jev import classify


def main() -> None:
    parser = argparse.ArgumentParser(prog="seller-cart")
    commands = parser.add_subparsers(dest="command", required=True)
    examples = commands.add_parser("examples", help="Проверить восемь учебных сценариев")
    grab = commands.add_parser("collect", help="Собрать карточки выбранного раздела в Яндекс.Браузере")
    grab.add_argument("--limit", type=int, default=40)
    grab.add_argument("--output", type=Path, default=Path("data/live_catalog.json"))
    grab.add_argument("--section", choices=tuple(SECTIONS), default="kanalizaciya")
    check = commands.add_parser("check", help="Проверить корзину из JSON")
    check.add_argument("input", type=Path)
    check.add_argument("--jev", action="store_true", help="Классифицировать запрос через JEV")
    check.add_argument("--stub", action="store_true", help="Использовать тестовую JEV-заглушку")
    check.add_argument("--jev-output", type=Path, default=Path("data/res_jev.json"), help="Файл для результата JEV (по умолчанию: data/res_jev.json)")
    args = parser.parse_args()
    if args.command == "examples":
        payload = json.loads(Path("data/scenarios.json").read_text(encoding="utf-8"))
        result = [{"id": case["id"], "expected": case["expected"],
                   "actual": check_cart(case["request"], case["products"])} for case in payload["scenarios"]]
    elif args.command == "collect":
        result = __import__("asyncio").run(collect(args.output, max(1, min(args.limit, 60)), section=args.section))
    else:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        cases = payload.get("scenarios")
        jev_results = []
        if cases is not None:
            result = []
            for case in cases:
                checked = check_cart(case["request"], case["products"])
                if args.jev:
                    jev_results.append({"id": case.get("id"), "request": case["request"],
                                        "result": classify(case["request"], use_stub=args.stub)})
                result.append({"id": case.get("id"), "expected": case.get("expected"), "actual": checked})
        else:
            result = check_cart(payload["request"], payload["products"])
            if args.jev:
                jev_results.append({"id": payload.get("id"), "request": payload["request"],
                                    "result": classify(payload["request"], use_stub=args.stub)})
        if args.jev:
            jev_output = args.jev_output
            jev_output.parent.mkdir(parents=True, exist_ok=True)
            jev_payload = jev_results[0] if len(jev_results) == 1 and cases is None else jev_results
            jev_output.write_text(json.dumps(jev_payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Результат JEV сохранён: {jev_output}", file=sys.stderr)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
