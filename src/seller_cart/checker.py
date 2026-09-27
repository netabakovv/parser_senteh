from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Finding:
    rule_id: str
    message: str
    source: str


def dimensions(text: str) -> set[int]:
    """Extract explicit millimetre sizes; do not infer sizes from SKU or title."""
    return {int(value) for value in re.findall(r"(?<!\w)(\d{2,4})\s*(?:мм|mm)\b", text, re.I)}


def check_cart(request: str, products: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[Finding] = []
    questions: list[str] = []

    request_sizes = dimensions(request)
    product_sizes = [(p, p.get("specs", {}).get("outer_diameter_mm")) for p in products]
    known_sizes = [(p, size) for p, size in product_sizes
                   if isinstance(size, (int, float)) and not isinstance(size, bool) and size > 0]
    roles = {p.get("role") for p in products}

    if not products:
        questions.append("Добавьте товары, которые нужно проверить.")
    elif not {"pipe", "fitting"}.issubset(roles):
        questions.append("Укажите в корзине трубу и фитинг, чтобы проверить их соединение.")
    if not request_sizes:
        questions.append("В запросе не указан явный размер существующей трубы (мм).")
    if any(size is None for _, size in product_sizes):
        questions.append("У одной или нескольких карточек неизвестен наружный диаметр.")
    elif len(known_sizes) != len(product_sizes):
        questions.append("В одной или нескольких карточках некорректно задан наружный диаметр.")

    if len(request_sizes) > 1:
        questions.append("В запросе указано несколько размеров; уточните размер соединения.")
    if len(request_sizes) == 1 and known_sizes and {"pipe", "fitting"}.issubset(roles):
        requested = next(iter(request_sizes))
        for product, size in known_sizes:
            if product.get("role") in {"pipe", "fitting"} and size != requested:
                errors.append(Finding(
                    "R1", f"{product['name']}: наружный диаметр {size} мм, в запросе требуется {requested} мм.",
                    "Источник R1: тестовое задание, часть 2 — пример совпадения диаметров соединяемых деталей; значения взяты из запроса и поля карточки.",
                ))
    # Compare explicit connection types on the pipe and fitting when the request specifies one.
    requested_joint = _explicit_joint(request)
    joint_products = [p for p in products if p.get("role") in {"pipe", "fitting"}]
    for product in joint_products:
        joint = product.get("specs", {}).get("connection_type")
        normalized_joint = _normalize_joint(str(joint)) if joint else None
        if requested_joint and normalized_joint and requested_joint != normalized_joint:
            errors.append(Finding(
                "R2", f"{product['name']}: тип соединения «{joint}», в запросе указан «{requested_joint}».",
                "Источник R2: тестовое задание, часть 2 — пример требования к типу соединения; сравниваются явные запрос и поле карточки.",
            ))
        elif requested_joint and not normalized_joint:
            questions.append(f"Для «{product['name']}» тип соединения неизвестен.")
    if requested_joint and not joint_products:
        questions.append("Укажите роли товара как pipe или fitting, чтобы проверить тип соединения.")

    if errors:
        status = "Требуется исправить"
    elif questions:
        status = "Нужно уточнить"
    else:
        status = "Проверенные условия выполнены"

    return {
        "status": status,
        "findings": [f.__dict__ for f in errors],
        "questions": list(dict.fromkeys(questions)),
        "note": "Проверены только перечисленные условия; результат не подтверждает полную комплектность или инженерную безопасность системы.",
    }


def _explicit_joint(text: str) -> str | None:
    return _normalize_joint(text)


def _normalize_joint(text: str) -> str | None:
    value = text.lower()
    if "аксиал" in value:
        return "аксиальный"
    if "пресс" in value:
        return "прессовый"
    if "резьб" in value:
        return "резьбовой"
    return None
