import unittest

from seller_cart.jev import classify, request_payload


class JevTests(unittest.TestCase):
    def test_payload_has_choice_options_including_unclear(self):
        payload = request_payload("Соединить трубу")
        self.assertEqual("jev-latest", payload["model"])
        self.assertEqual({"connect_pipe_fitting", "other", "unclear"}, set(payload["questions"]["task"]["criteria"]))

    def test_confident_stub_result_is_accepted(self):
        self.assertEqual("connect_pipe_fitting", classify("Соединить трубу с фитингом", use_stub=True)["choice"])

    def test_low_confidence_becomes_unclear(self):
        self.assertEqual("unclear", classify("Какая-то непонятная задача", use_stub=True)["choice"])

    def test_underspecified_request_becomes_unclear(self):
        self.assertEqual("unclear", classify("Нужно подобрать фитинг для соединения трубы", use_stub=True)["choice"])


if __name__ == "__main__":
    unittest.main()
