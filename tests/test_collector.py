import unittest
import json
import tempfile
from pathlib import Path

from patchright.async_api import async_playwright

from seller_cart.collector import CATEGORY_CARDS_JS, _parse_cards, _sanitize_saved_html, _save, parse_saved_html, select_product_urls


class UrlSelectionTests(unittest.TestCase):
    def test_only_product_urls_from_selected_catalog_are_kept(self):
        urls = [
            "https://477477.ru/catalog/kanalizaciya/akvatek-15",
            "https://477477.ru/catalog/kanalizaciya/podrazdel/vnutrennyaya-kanalizaciya",
            "https://477477.ru/catalog/truby-i-fitingi/fitting-20",
            "https://other.example/catalog/kanalizaciya/fake-product",
            "https://477477.ru/catalog/kanalizaciya-extra/wrong-category",
        ]
        self.assertEqual([urls[0]], select_product_urls(urls))

    def test_save_keeps_previous_report_runs_and_writes_array(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "products.json"
            _save(output, [{"name": "first"}], {"run": 1})
            _save(output, [{"name": "second"}], {"run": 2})
            report = json.loads((Path(directory) / "collection_report.json").read_text(encoding="utf-8"))
            products = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual([{"run": 1}, {"run": 2}], report["runs"])
        self.assertEqual([{"name": "second"}], products)

    def test_parse_counts_skip_reasons_and_unique_urls(self):
        base = {"name": "Pipe", "manufacturer": "Brand", "category": "Pipes",
                "characteristics": None, "raw_badges": [], "article": None}
        cards = [
            {**base, "url": "https://477477.ru/catalog/kanalizaciya/a"},
            {**base, "url": "https://477477.ru/catalog/kanalizaciya/a"},
            {**base, "url": "https://477477.ru/catalog/truby-i-fitingi/b"},
            {**base, "url": None},
            {**base, "url": "https://477477.ru/catalog/kanalizaciya/c", "name": None},
            {**base, "url": "https://477477.ru/catalog/kanalizaciya/d"},
        ]
        records, skipped, unique = _parse_cards(cards, "kanalizaciya", "now", 1, set())
        self.assertEqual(1, len(records))
        self.assertEqual(4, len(unique))
        self.assertEqual({"duplicate_url": 1, "outside_section": 1, "missing_url": 1,
                          "missing_name": 1, "limit_reached": 1}, skipped)


class CardParserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)
        self.page = await self.browser.new_page()

    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()

    async def test_extracts_list_card_fields_and_next_page(self):
        await self.page.set_content('''
          <base href="https://477477.ru/">
          <section><div class="product-list-grid">
            <a class="product-list-card" href="/catalog/kanalizaciya/test-pipe-50">
              <span class="brand-line">Учебный бренд</span><h3>Учебная труба</h3>
              <p>Описание трубы. Ассортимент: вода. Артикул TEST-50.</p>
              <ul class="badges"><li class="badge">Артикул: TEST-50</li><li class="badge">Диаметр: 50</li>
                  <li class="badge">Тип: труба</li><li class="badge price-badge">Цена по запросу</li></ul>
            </a>
          </div></section>
          <nav class="catalog-pagination"><a rel="next" href="/catalog/kanalizaciya?page=2">Вперёд</a></nav>
        ''')
        listing = await self.page.evaluate(CATEGORY_CARDS_JS)
        card = listing["cards"][0]
        self.assertEqual("https://477477.ru/catalog/kanalizaciya/test-pipe-50", card["url"])
        self.assertEqual("Учебная труба", card["name"])
        self.assertEqual("Учебный бренд", card["manufacturer"])
        self.assertEqual("TEST-50", card["article"])
        self.assertEqual("труба", card["category"])
        self.assertEqual("вода", card["characteristics"]["Ассортимент"])
        self.assertEqual("50", card["characteristics"]["Диаметр"])
        self.assertEqual(["Артикул: TEST-50", "Диаметр: 50", "Тип: труба", "Цена по запросу"], card["raw_badges"])
        self.assertEqual("https://477477.ru/catalog/kanalizaciya?page=2", listing["next_page"])

    async def test_missing_article_and_specs_stay_unknown(self):
        await self.page.set_content('''
          <base href="https://477477.ru/">
          <div class="product-list-grid"><a class="product-list-card" href="/catalog/kanalizaciya/test-110">
            <span class="brand-line">Brand</span><h3>Труба 110</h3><p>Ассортимент: без артикула</p>
          </a></div>
        ''')
        card = (await self.page.evaluate(CATEGORY_CARDS_JS))["cards"][0]
        self.assertIsNone(card["article"])
        self.assertEqual("без артикула", card["characteristics"]["Ассортимент"])
        self.assertNotIn("Диаметр", card["characteristics"])

    async def test_absent_characteristics_and_article_are_null(self):
        await self.page.set_content('''
          <a class="product-list-card" href="/catalog/kanalizaciya/plain-item">
            <h3>Без характеристик</h3>
          </a>
        ''')
        card = (await self.page.evaluate(CATEGORY_CARDS_JS))["cards"][0]
        self.assertIsNone(card["article"])
        self.assertIsNone(card["category"])
        self.assertIsNone(card["characteristics"])
        self.assertEqual([], card["raw_badges"])

    async def test_saved_html_is_sanitized_then_written_as_json(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "page.html"
            output = Path(directory) / "products.json"
            source.write_text('''
              <script>window.__untrusted = true</script>
              <img src="https://example.invalid/image.png" onerror="alert(1)">
              <a class="product-list-card" href="/catalog/kanalizaciya/test">
                <span class="brand-line">Brand</span><h3>Товар</h3>
                <ul class="badges"><li class="badge">Диаметр: 50</li></ul>
              </a>
              <a class="product-list-card" href="https://evil.example/catalog/kanalizaciya/fake">
                <span class="brand-line">Fake</span><h3>Не товар сайта</h3>
              </a>
            ''', encoding="utf-8")
            safe = _sanitize_saved_html(source.read_text(encoding="utf-8"))
            self.assertNotIn("window.__untrusted", safe)
            self.assertNotIn("example.invalid", safe)
            report = await parse_saved_html(source, output)
            data = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(1, report["parsed"])
        self.assertEqual((2, 1, 1, 2), (report["found"], report["parsed"], report["skipped"], report["unique_urls"]))
        self.assertEqual({"outside_section": 1}, report["skip_reasons"])
        self.assertEqual("saved_product_card_html", report["source_type"])
        self.assertEqual("Товар", data[0]["name"])
        self.assertEqual("50", data[0]["characteristics"]["Диаметр"])
        self.assertEqual("https://477477.ru/catalog/kanalizaciya/test", data[0]["url"])
        self.assertTrue(data[0]["collected_at"])

    async def test_saved_html_caps_output_at_60_and_counts_skips(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "listing.html"
            output = Path(directory) / "products.json"
            cards = "".join(
                f'<a class="product-list-card" href="/catalog/kanalizaciya/item-{i}"><h3>Item {i}</h3></a>'
                for i in range(61)
            )
            source.write_text(f'<div>{cards}</div>', encoding="utf-8")
            report = await parse_saved_html(source, output, limit=200)
            products = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual((61, 60, 1, 61),
                         (report["found"], report["parsed"], report["skipped"], report["unique_urls"]))
        self.assertEqual({"limit_reached": 1}, report["skip_reasons"])
        self.assertEqual(60, len(products))


if __name__ == "__main__":
    unittest.main()
