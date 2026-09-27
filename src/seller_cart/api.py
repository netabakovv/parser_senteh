from __future__ import annotations

import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .checker import _explicit_joint, _normalize_joint
from .jev import classify

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "catalog.json"
RULE_DIAMETER = "Сверяются только явно указанные диаметры из запроса и карточек товара."
RULE_JOINT = "Сверяется только явно указанное требование к типу соединения и значение карточки."


def _characteristics(product: dict[str, Any]) -> dict[str, Any]:
    value = product.get("characteristics")
    return value if isinstance(value, dict) else {}


def _product_diameter(product: dict[str, Any]) -> int | None:
    for key, value in _characteristics(product).items():
        if any(word in str(key).lower() for word in ("диаметр", "размер")):
            diameter = _diameter_value(value)
            if diameter is not None:
                return diameter
    name = str(product.get("name") or "")
    diameter = _diameter_value(name)
    if diameter is not None:
        return diameter
    # Some catalog titles use a terminal size designation, e.g. "заглушка 22".
    match = re.search(r"\bзаглушка\s+(\d{2,4})\s*$", name, re.I)
    if match:
        return int(match.group(1))
    return None


def _diameter_value(value: Any) -> int | None:
    text = str(value)
    sizes = [int(value) for value in re.findall(r"(?<!\d)(\d{2,3})\s*(?:[xх×]|мм\b)", text, re.I)]
    if sizes:
        return max(sizes)
    match = re.fullmatch(r"\s*(\d{2,3})\s*", text)
    return int(match.group(1)) if match else None


def _request_diameter(text: str) -> int | None:
    # In a plumbing request, a size immediately after "трубу" is its diameter in mm.
    match = re.search(r"\bтруб\w*\s+(\d{2,3})\b", text, re.I)
    return int(match.group(1)) if match else _diameter_value(text)


def _fact(label: str, value: Any, source: str) -> dict[str, str]:
    return {"label": label, "value": str(value) if value is not None else "Не указано", "source": source}


def check_request(payload: dict[str, Any]) -> dict[str, Any]:
    request = payload.get("buyer_request")
    products = payload.get("products")
    clarifications = payload.get("clarifications", {})
    if not isinstance(request, str) or not isinstance(products, list) or not all(isinstance(p, dict) for p in products):
        raise ValueError("Ожидаются buyer_request и массив products с объектами товаров.")
    if not isinstance(clarifications, dict):
        raise ValueError("clarifications должен быть объектом.")
    requested_diameter = _request_diameter(request)
    diameter_source = "buyer_request"
    if requested_diameter is None and "existing_pipe_diameter" in clarifications:
        clarified = _diameter_value(clarifications["existing_pipe_diameter"])
        if clarified is not None:
            requested_diameter = clarified
            diameter_source = "clarification"
    sizes = [_product_diameter(p) for p in products]
    diameter_status = "unknown"
    diameter_message = "Нужны товары и явные данные о диаметрах, чтобы сравнить размеры."
    diameter_recommendation = "Добавьте товары с указанным размером или уточните диаметр существующей трубы."
    if not products:
        diameter_message = "Корзина пуста."
    elif requested_diameter is not None and all(size is not None for size in sizes):
        mismatch = [i for i, size in enumerate(sizes) if size != requested_diameter]
        diameter_status = "failed" if mismatch else "passed"
        diameter_message = ("Диаметры совпадают." if not mismatch else
                            "Не совпадает размер: " + "; ".join(f"{products[i].get('name', 'Товар')}: {sizes[i]} мм вместо {requested_diameter} мм" for i in mismatch) + ".")
        diameter_recommendation = "Подберите товары с требуемым диаметром." if mismatch else None
    diameter_facts = [_fact("Диаметр из запроса", f"{requested_diameter} мм" if requested_diameter else None,
                            diameter_source)]
    diameter_facts += [_fact(f"Диаметр: {p.get('name') or 'Товар'}", f"{s} мм" if s else None, "product") for p, s in zip(products, sizes)]

    requested_joint = _explicit_joint(request)
    joints: list[str | None] = []
    for product in products:
        joint = next((v for k, v in _characteristics(product).items() if "соедин" in str(k).lower()), None)
        joints.append(_normalize_joint(str(joint)) if joint else None)
    joint_status = "unknown"
    joint_message = "В запросе нет явного требования к типу соединения; карточки не сравнивались."
    joint_recommendation = None
    if requested_joint:
        joint_message = "Не удалось проверить тип соединения: в карточках нет явных данных."
        joint_recommendation = "Уточните тип соединения в карточках товара."
        if products and all(j is not None for j in joints):
            joint_status = "failed" if any(j != requested_joint for j in joints) else "passed"
            joint_message = "Тип соединения совпадает с запросом." if joint_status == "passed" else f"В запросе указан тип «{requested_joint}», но в карточке есть другое значение."
            joint_recommendation = "Подберите товар с требуемым типом соединения." if joint_status == "failed" else None
    joint_facts = [_fact("Тип соединения из запроса", requested_joint, "buyer_request")]
    joint_facts += [_fact(f"Тип соединения: {p.get('name') or 'Товар'}", j, "product") for p, j in zip(products, joints)]
    checks = [
        {"id": "diameter_match", "title": "Совпадение диаметра", "status": diameter_status,
         "message": diameter_message, "recommendation": diameter_recommendation, "facts": diameter_facts, "source_quote": RULE_DIAMETER},
        {"id": "connection_type_match", "title": "Тип соединения", "status": joint_status,
         "message": joint_message, "recommendation": joint_recommendation, "facts": joint_facts, "source_quote": RULE_JOINT},
    ]
    questions = []
    if requested_diameter is None and products:
        questions.append({"key": "existing_pipe_diameter", "question": "Какой диаметр существующей трубы?", "type": "text"})
    if requested_joint and any(j is None for j in joints):
        questions.append({"key": "connection_type", "question": "Какой тип соединения указан для товара?", "type": "text"})
    status = "needs_fix" if any(c["status"] == "failed" for c in checks) else "need_clarification" if questions or diameter_status == "unknown" else "passed"
    jev = []
    try:
        use_stub = not bool(os.environ.get("JEV_API_KEY"))
        result = classify(request, use_stub=use_stub)
        choice = result["choice"]
        probability = result.get("probabilities", {}).get(choice, result.get("confidence", 0))
        jev.append({"question": "Какую задачу описывает покупатель?", "result": choice,
                    "model": "jev-latest (локальная заглушка)" if use_stub else "jev-latest", "probability": probability,
                    "status": "уверенно" if probability >= .75 else "неясно"})
    except (ValueError, TypeError, KeyError):
        pass
    return {"status": status, "checks": checks, "questions": questions, "jev": jev}


class Handler(BaseHTTPRequestHandler):
    def _json(self, status: int, value: Any) -> None:
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self) -> None:
        self._json(204, {})

    def do_GET(self) -> None:
        if urlparse(self.path).path != "/api/catalog":
            self._json(404, {"error": "Not found"})
            return
        try:
            catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
            if not isinstance(catalog, list):
                raise ValueError("Ожидался массив товаров")
            self._json(200, catalog)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            self._json(500, {"error": f"Каталог не загружен: {exc}"})

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/check":
            self._json(404, {"error": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1_000_000:
                raise ValueError("Некорректный размер JSON тела")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Ожидался JSON-объект")
            self._json(200, check_request(payload))
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            self._json(400, {"error": f"Некорректный запрос: {exc}"})


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Локальный API проверки корзины")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    print(f"Seller cart API: http://{args.host}:{args.port}")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
