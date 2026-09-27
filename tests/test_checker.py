import json
import unittest
from pathlib import Path

from seller_cart.checker import check_cart, dimensions


class CheckerTests(unittest.TestCase):
    def test_documented_scenarios_match(self):
        data = json.loads(Path("data/scenarios.json").read_text(encoding="utf-8"))
        for case in data["scenarios"]:
            with self.subTest(case=case["id"]):
                self.assertEqual(case["expected"], check_cart(case["request"], case["products"])["status"])

    def test_dimension_extractor_requires_unit(self):
        self.assertEqual({20, 25}, dimensions("Труба 20 мм, переход на 25mm"))
        self.assertEqual(set(), dimensions("Артикул FA200002"))

    def test_size_mismatch_is_error(self):
        result = check_cart("Соединить трубу 20 мм", [
            {"name":"Труба", "role":"pipe", "specs":{"outer_diameter_mm":20}},
            {"name":"Фитинг", "role":"fitting", "specs":{"outer_diameter_mm":25}},
        ])
        self.assertEqual("Требуется исправить", result["status"])
        self.assertIn("25 мм", result["findings"][0]["message"])

    def test_two_fittings_do_not_confirm_connection(self):
        result = check_cart("Соединить трубу 20 мм", [
            {"name":"Фитинг 1", "role":"fitting", "specs":{"outer_diameter_mm":20}},
            {"name":"Фитинг 2", "role":"fitting", "specs":{"outer_diameter_mm":20}},
        ])
        self.assertEqual("Нужно уточнить", result["status"])

    def test_missing_product_size_does_not_confirm(self):
        result = check_cart("Соединить трубу 20 мм", [
            {"name":"Труба", "role":"pipe", "specs":{"outer_diameter_mm":20}},
            {"name":"Фитинг", "role":"fitting", "specs":{"outer_diameter_mm":None}},
        ])
        self.assertEqual("Нужно уточнить", result["status"])

    def test_missing_request_size_does_not_confirm(self):
        result = check_cart("Подобрать соединение", [
            {"name":"Труба", "role":"pipe", "specs":{"outer_diameter_mm":20}},
            {"name":"Фитинг", "role":"fitting", "specs":{"outer_diameter_mm":20}},
        ])
        self.assertEqual("Нужно уточнить", result["status"])

    def test_missing_request_size_does_not_infer_mismatch_from_two_cards(self):
        result = check_cart("Подобрать соединение трубы с фитингом", [
            {"name":"Труба", "role":"pipe", "specs":{"outer_diameter_mm":20}},
            {"name":"Фитинг", "role":"fitting", "specs":{"outer_diameter_mm":25}},
        ])
        self.assertEqual("Нужно уточнить", result["status"])
        self.assertEqual([], result["findings"])

    def test_unknown_roles_do_not_confirm(self):
        result = check_cart("Соединить трубу 20 мм", [
            {"name":"Труба", "specs":{"outer_diameter_mm":20}},
            {"name":"Фитинг", "specs":{"outer_diameter_mm":20}},
        ])
        self.assertEqual("Нужно уточнить", result["status"])

    def test_connection_type_word_forms_are_normalized(self):
        result = check_cart("Соединить трубу 20 мм аксиальным фитингом", [
            {"name":"Труба", "role":"pipe", "specs":{"outer_diameter_mm":20,"connection_type":"аксиальный"}},
            {"name":"Фитинг", "role":"fitting", "specs":{"outer_diameter_mm":20,"connection_type":"аксиальное соединение"}},
        ])
        self.assertEqual("Проверенные условия выполнены", result["status"])

    def test_unrecognized_connection_type_requests_clarification(self):
        result = check_cart("Соединить трубу 20 мм аксиально", [
            {"name":"Труба", "role":"pipe", "specs":{"outer_diameter_mm":20}},
            {"name":"Фитинг", "role":"fitting", "specs":{"outer_diameter_mm":20,"connection_type":"неизвестный тип"}},
        ])
        self.assertEqual("Нужно уточнить", result["status"])


if __name__ == "__main__":
    unittest.main()
