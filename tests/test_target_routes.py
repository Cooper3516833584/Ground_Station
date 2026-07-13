import unittest

from target_routes import (
    LANDING_POINT,
    START_POINT,
    TOP_CORRIDOR_Y,
    all_location_codes,
    cycle_location_codes,
    parse_location,
    parse_target_message,
    route_for,
)


class TargetRouteTests(unittest.TestCase):
    def test_all_24_locations_are_available_in_face_order(self):
        codes = all_location_codes()
        self.assertEqual(len(codes), 24)
        self.assertEqual(codes[:6], ("A1", "A2", "A3", "A4", "A5", "A6"))
        self.assertEqual(codes[-1], "D6")

    def test_left_to_right_order_is_mirrored_on_opposite_faces(self):
        self.assertEqual(parse_location("A1").point[1], 250.0)
        self.assertEqual(parse_location("A3").point[1], 150.0)
        self.assertEqual(parse_location("B1").point[1], 150.0)
        self.assertEqual(parse_location("B3").point[1], 250.0)
        self.assertEqual(parse_location("C1").point[1], 250.0)
        self.assertEqual(parse_location("D1").point[1], 150.0)

    def test_top_and_bottom_rows_share_xy_but_have_different_heights(self):
        top = parse_location("C2")
        bottom = parse_location("C5")
        self.assertEqual(top.point, bottom.point)
        self.assertEqual(top.height_cm, 140.0)
        self.assertEqual(bottom.height_cm, 100.0)

    def test_every_route_enters_from_positive_y_end_and_returns_to_landing(self):
        for code in all_location_codes():
            route = route_for(code)
            self.assertEqual(route.outbound[0], START_POINT)
            self.assertEqual(route.outbound[1], (START_POINT[0], TOP_CORRIDOR_Y))
            self.assertEqual(route.outbound[-1], route.target.point)
            self.assertGreater(route.outbound[-2][1], route.target.point[1])
            self.assertEqual(route.return_path[0], route.target.point)
            self.assertGreater(route.return_path[1][1], route.target.point[1])
            self.assertEqual(route.return_path[-1], LANDING_POINT)

    def test_invalid_location_is_rejected(self):
        for code in ("", "A0", "A7", "E1", "AA1", "1A"):
            with self.assertRaises(ValueError):
                parse_location(code)

    def test_three_round_cycle_contains_72_routes_in_order(self):
        codes = cycle_location_codes(3)
        self.assertEqual(len(codes), 72)
        self.assertEqual(codes[:3], ("A1", "A2", "A3"))
        self.assertEqual(codes[23:26], ("D6", "A1", "A2"))
        self.assertEqual(codes[-1], "D6")

    def test_cycle_rejects_non_positive_round_count(self):
        with self.assertRaises(ValueError):
            cycle_location_codes(0)

    def test_target_mission_status_messages_are_parsed(self):
        detected = parse_target_message("TGT:DETECTED:17:B3")
        self.assertEqual(detected.kind, "DETECTED")
        self.assertEqual(detected.cargo_number, 17)
        self.assertEqual(detected.location, "B3")
        countdown = parse_target_message("TGT:COUNTDOWN:10:17:B3")
        self.assertEqual(countdown.seconds, 10)
        self.assertEqual(parse_target_message("INV:ITEM:17:B3"), None)

    def test_invalid_target_mission_status_is_ignored(self):
        self.assertIsNone(parse_target_message("TGT:DETECTED:25:A1"))
        self.assertIsNone(parse_target_message("TGT:COUNTDOWN:x:1:A1"))
        self.assertIsNone(parse_target_message("TGT:ARRIVED:1:E1"))


if __name__ == "__main__":
    unittest.main()
