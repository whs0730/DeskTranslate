import unittest

from desktranslate.geometry import intersects_visible_area, sanitize_window_geometry

PRIMARY = [(0, 0, 1920, 1040)]
DUAL = [(-1920, 0, 0, 1080), (0, 0, 1920, 1040)]
SIZE = {"min_width": 320, "min_height": 240, "default_width": 520, "default_height": 420}


class GeometryTests(unittest.TestCase):
    def test_minimized_sentinel_is_centered_at_default_size(self):
        # 用户机器上记录到的最小化几何：x = y = -21333、158×26。
        x, y, width, height = sanitize_window_geometry(-21333, -21333, 158, 26, PRIMARY, **SIZE)

        self.assertEqual((width, height), (520, 420))
        self.assertEqual((x, y), (700, 206))
        self.assertTrue(intersects_visible_area(x, y, width, height, PRIMARY))

    def test_saved_position_and_size_are_kept(self):
        result = sanitize_window_geometry(200, 150, 520, 420, PRIMARY, **SIZE)

        self.assertEqual(result, (200, 150, 520, 420))

    def test_size_between_minimum_and_default_is_kept(self):
        result = sanitize_window_geometry(200, 150, 400, 300, PRIMARY, **SIZE)

        self.assertEqual(result, (200, 150, 400, 300))

    def test_left_monitor_position_is_kept(self):
        result = sanitize_window_geometry(-1500, 200, 520, 420, DUAL, **SIZE)

        self.assertEqual(result, (-1500, 200, 520, 420))

    def test_window_dragged_off_screen_is_centered(self):
        x, y, width, height = sanitize_window_geometry(1900, -300, 520, 420, PRIMARY, **SIZE)

        # 工作区 1920×1040、窗口 520×420：水平居中、垂直偏上三分之一。
        self.assertEqual((x, y, width, height), (700, 206, 520, 420))
        self.assertTrue(intersects_visible_area(x, y, width, height, PRIMARY))

    def test_partially_offscreen_window_is_kept(self):
        # 拖到左边只露出大部分时保持用户位置，不要强行挪动。
        result = sanitize_window_geometry(-30, 25, 720, 480, PRIMARY, **SIZE)

        self.assertEqual(result, (-30, 25, 720, 480))

    def test_missing_position_is_left_to_webview(self):
        result = sanitize_window_geometry(None, None, None, None, PRIMARY, **SIZE)

        self.assertEqual(result, (None, None, 520, 420))

    def test_no_screen_information_keeps_size_only(self):
        result = sanitize_window_geometry(10, 20, 520, 420, [], **SIZE)

        self.assertEqual(result, (None, None, 520, 420))


if __name__ == "__main__":
    unittest.main()