import json
from pathlib import Path
import unittest
from types import SimpleNamespace

from components.fleet_models import NodeFlags, TerrainCode
from screen_start_bridge import (
    WHITE_BRIGHTNESS,
    drone_is_airborne,
    field_heading_to_math_ccw,
    nearest_water_global,
    survey_cell_to_global,
)


class DisasterSurveyCoordinateTests(unittest.TestCase):
    def test_full_white_matches_led_daemon_brightness_limit(self):
        self.assertEqual(20, WHITE_BRIGHTNESS)

    def test_survey_cells_use_drone_global_centres(self):
        self.assertEqual((115, 175), survey_cell_to_global(0, 0))
        self.assertEqual((395, 315), survey_cell_to_global(2, 4))

    def test_nearest_lake_or_river_is_selected_from_car_start(self):
        terrain = [int(TerrainCode.FIELD)] * 15
        terrain[0] = int(TerrainCode.RIVER)
        terrain[14] = int(TerrainCode.LAKE)
        self.assertEqual(
            (115, 175), nearest_water_global(tuple(terrain), (160, 50))
        )

    def test_reported_absolute_positions_override_fallback_grid(self):
        positions = tuple((1000 + index, 2000 + index) for index in range(15))
        self.assertEqual((1014, 2014), survey_cell_to_global(2, 4, positions))

    def test_field_zero_up_converts_to_internal_positive_y(self):
        self.assertEqual(90.0, field_heading_to_math_ccw(0.0))

    def test_mapping_gate_requires_armed_fresh_positive_altitude(self):
        node = SimpleNamespace(
            online=True,
            stale=False,
            node_flags=int(NodeFlags.ARMED_OR_MOTOR_ACTIVE),
            z_cm=15,
        )
        self.assertTrue(drone_is_airborne(node, 10))
        node.z_cm = 5
        self.assertFalse(drone_is_airborne(node, 10))
        node.z_cm = 15
        node.stale = True
        self.assertFalse(drone_is_airborne(node, 10))

    def test_missing_water_is_rejected(self):
        terrain = (int(TerrainCode.FIELD),) * 15
        with self.assertRaises(ValueError):
            nearest_water_global(terrain, (0, 0))

    def test_takeoff_countdown_and_alarm_lead_are_configured(self):
        path = Path(__file__).resolve().parents[1] / "fleet_config.json"
        config = json.loads(path.read_text(encoding="utf-8"))["disaster_survey"]
        self.assertEqual(20.0, config["start_delay_seconds"])
        self.assertEqual(5.0, config["takeoff_alarm_seconds"])
        self.assertLess(
            config["takeoff_alarm_seconds"],
            config["start_delay_seconds"],
        )


if __name__ == "__main__":
    unittest.main()
