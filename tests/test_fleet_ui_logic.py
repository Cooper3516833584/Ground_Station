import unittest


class FleetUiLogicTests(unittest.TestCase):
    def test_point_in_polygon_without_loading_qt(self):
        try:
            from components.ui.fleet_main_window import point_in_polygon
        except ImportError:
            self.skipTest("PyQt5 is not installed in this development environment")
        polygon = ((0, 0), (100, 0), (100, 100), (0, 100))
        self.assertTrue(point_in_polygon((50, 50), polygon))
        self.assertFalse(point_in_polygon((150, 50), polygon))


if __name__ == "__main__":
    unittest.main()
