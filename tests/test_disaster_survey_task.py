import json
from pathlib import Path
import threading
import unittest
from types import SimpleNamespace

from components.fleet_models import NodeFlags, TerrainCode
from screen_start_bridge import (
    ScreenStartBridge,
    WHITE_BRIGHTNESS,
    drone_is_airborne,
    field_heading_to_math_ccw,
    nearest_water_global,
    survey_cell_to_global,
)


class DisasterSurveyCoordinateTests(unittest.TestCase):
    def test_survey_polling_stays_off_before_start_command_is_accepted(self):
        class Master:
            def __init__(self):
                self.requests = 0

            def request_survey(self, _node_id):
                self.requests += 1

        bridge = ScreenStartBridge.__new__(ScreenStartBridge)
        bridge._lock = threading.Lock()
        bridge._survey_polling_enabled = False
        bridge._survey_future = None
        bridge._next_survey_at = 0.0
        bridge._master = Master()

        bridge.tick()

        self.assertEqual(0, bridge._master.requests)

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

    def test_car_rescue_uses_startup_local_three_point_route(self):
        path = Path(__file__).resolve().parents[1] / "fleet_config.json"
        config = json.loads(path.read_text(encoding="utf-8"))["disaster_survey"]
        self.assertEqual([0, 0], config["car_start_global_cm"])
        self.assertEqual([[25, 105], [95, 175]], config["car_rescue_points_cm"])

        bridge = ScreenStartBridge.__new__(ScreenStartBridge)
        bridge._transport = SimpleNamespace()
        bridge._master = SimpleNamespace()
        bridge._store = SimpleNamespace()
        bridge._led = SimpleNamespace()
        bridge._cooldown_seconds = 0.0
        ScreenStartBridge.__init__(
            bridge,
            transport=bridge._transport,
            master=bridge._master,
            store=bridge._store,
            mission_config=config,
            cooldown_seconds=0.0,
            led=bridge._led,
        )
        self.assertEqual((0, 0), bridge._car_start)
        self.assertEqual(((25, 105), (95, 175)), bridge._car_rescue_points)


if __name__ == "__main__":
    unittest.main()
