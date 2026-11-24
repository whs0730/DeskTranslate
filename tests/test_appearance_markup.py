"""首帧外观注入：页面还没跑 JS 时，<html> 上就该是用户设置的外观。"""

from __future__ import annotations

import re
import unittest

from desktranslate.config import AppSettings
from desktranslate.ui import appearance_attributes, glass_variables, with_appearance

MARKUP = '<!DOCTYPE html>\n<html lang="zh-CN">\n<head><meta charset="UTF-8"></head>\n<body></body>\n</html>\n'


class AppearanceAttributesTests(unittest.TestCase):
    def test_matches_the_values_applySettings_writes(self) -> None:
        settings = AppSettings(frosted_amount=60, liquid_transparency=40, liquid_color="#4c5057")
        self.assertEqual(
            glass_variables(settings),
            "--frost-blur:9.6px;--frost-tint:0.51;--liquid-alpha:0.42;--liquid-color:#4c5057",
        )

    def test_frosted_appearance_only_flags_native_glass(self) -> None:
        attributes = appearance_attributes(AppSettings(appearance="frosted"), native_glass=True)
        self.assertIn('class="native-glass"', attributes)
        self.assertNotIn("appearance-", attributes)

    def test_liquid_and_transparent_appearances_are_exclusive(self) -> None:
        liquid = appearance_attributes(AppSettings(appearance="liquid"), native_glass=True)
        transparent = appearance_attributes(AppSettings(appearance="transparent"), native_glass=True)
        self.assertIn("appearance-liquid", liquid)
        self.assertNotIn("appearance-transparent", liquid)
        self.assertIn("appearance-transparent", transparent)
        self.assertNotIn("appearance-liquid", transparent)

    def test_without_native_glass_no_class_is_emitted(self) -> None:
        attributes = appearance_attributes(AppSettings(appearance="frosted"), native_glass=False)
        self.assertNotIn("class=", attributes)
        self.assertIn("--liquid-color:", attributes)


class WithAppearanceTests(unittest.TestCase):
    def test_attributes_land_on_the_html_tag_and_keep_lang(self) -> None:
        out = with_appearance(MARKUP, appearance_attributes(AppSettings(), native_glass=True))
        tag = re.search(r"<html[^>]*>", out)
        assert tag is not None
        self.assertIn("native-glass", tag.group(0))
        self.assertIn('lang="zh-CN"', tag.group(0))
        self.assertEqual(out.count("<html"), 1)
        # 除 <html> 之外的内容保持原样。
        self.assertIn("<head><meta charset=\"UTF-8\"></head>", out)

    def test_markup_without_html_tag_is_returned_untouched(self) -> None:
        self.assertEqual(with_appearance("<div></div>", 'class="x"'), "<div></div>")


if __name__ == "__main__":
    unittest.main()