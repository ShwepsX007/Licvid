"""Исходники шкалы расчётных уровней: кнопка без мишени, полосы не поверх цены."""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class LiqLevelsUiSourceTest(unittest.TestCase):
    def test_chart_button_has_no_target_emoji(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        start = html.find('id="levels-toggle"')
        end = html.find("</button>", start)
        button = html[start:end]
        self.assertNotIn("🎯", button)
        self.assertIn("оценк", button.lower())

        src = (ROOT / "static" / "i18n.js").read_text(encoding="utf-8")
        # подпись кнопки слоя — chart.levels, без эмодзи во всех языках
        for chunk in src.split('"chart.levels":')[1:]:
            value = chunk.split('"', 2)[1]
            self.assertNotIn("🎯", value)

    def test_scale_sits_beside_price_axis(self):
        src = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        fn = src.split("function drawLiqLevels", 1)[1].split("\n    function ", 1)[0]
        self.assertIn("plotRight", fn)
        self.assertIn("levelsPlotBox", fn)
        # полоса растёт влево от края поля, а не от правого края холста
        self.assertNotIn("W - w", fn)
        self.assertIn("plotRight - w", fn)
        self.assertIn("priceScale", src.split("function priceAxisWidthPx", 1)[1][:800])
