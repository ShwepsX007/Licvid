import unittest
from fastapi.testclient import TestClient
import server

class SeoRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(server.app)

    def test_robots_txt(self):
        r = self.client.get("/robots.txt")
        self.assertEqual(r.status_code, 200)
        self.assertIn("User-agent: *", r.text)
        self.assertIn("Sitemap:", r.text)
        self.assertIn("Disallow: /admin", r.text)

    def test_sitemap_xml(self):
        r = self.client.get("/sitemap.xml")
        self.assertEqual(r.status_code, 200)
        self.assertIn("<urlset", r.text)
        self.assertIn("hreflang=\"en\"", r.text)
        self.assertIn("hreflang=\"zh-Hans\"", r.text)
        self.assertIn("hreflang=\"es\"", r.text)
        self.assertIn("hreflang=\"hi\"", r.text)
        self.assertIn("hreflang=\"ru\"", r.text)

    def test_root_landing_en(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn('lang="en"', r.text)
        self.assertIn('LiqScope — Real-Time Crypto Futures Liquidation Terminal', r.text)
        self.assertIn('rel="canonical"', r.text)
        self.assertIn('application/ld+json', r.text)
        self.assertIn('FAQPage', r.text)

    def test_localized_landing_ru(self):
        r = self.client.get("/ru")
        self.assertEqual(r.status_code, 200)
        self.assertIn('lang="ru"', r.text)
        self.assertIn('терминал ликвидаций', r.text)
        self.assertIn('hreflang="ru"', r.text)

    def test_localized_landing_zh(self):
        r = self.client.get("/zh")
        self.assertEqual(r.status_code, 200)
        self.assertIn('lang="zh-Hans"', r.text)
        self.assertIn('实时加密货币期货爆仓终端与K线图', r.text)

    def test_localized_terminal(self):
        r = self.client.get("/zh/terminal")
        self.assertEqual(r.status_code, 200)
        self.assertIn('lang="zh-Hans"', r.text)
        self.assertIn('实时爆仓流', r.text)

    def test_localized_digest(self):
        r = self.client.get("/es/digest")
        self.assertEqual(r.status_code, 200)
        self.assertIn('lang="es"', r.text)
        self.assertIn('Resumen Diario del Mercado', r.text)

    def test_404_on_unknown_lang(self):
        r = self.client.get("/unknown_lang_xyz")
        self.assertEqual(r.status_code, 404)

if __name__ == "__main__":
    unittest.main()
