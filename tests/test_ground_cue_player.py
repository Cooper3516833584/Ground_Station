import unittest

from components.ground_cue_player import GroundCuePlayer


class FakeLed:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    def solid(self, color, brightness=3):
        self.calls.append(("solid", tuple(color), brightness))
        if self.fail == "solid":
            raise RuntimeError("solid failed")

    def off(self):
        self.calls.append(("off",))
        if self.fail == "off":
            raise RuntimeError("off failed")

    def flow(self):
        self.calls.append(("flow",))
        if self.fail == "flow":
            raise RuntimeError("flow failed")


class GroundCuePlayerTests(unittest.TestCase):
    def test_escort_cue_is_three_white_pulses(self):
        led = FakeLed()
        buzzes = []
        waits = []
        player = GroundCuePlayer(
            led=led,
            buzzer=buzzes.append,
            wait=waits.append,
        )

        player.play_mission1_escort_acquired()

        self.assertEqual(
            [
                ("solid", (255, 255, 255), 20),
                ("off",),
                ("solid", (255, 255, 255), 20),
                ("off",),
                ("solid", (255, 255, 255), 20),
                ("off",),
            ],
            led.calls,
        )
        self.assertEqual([0.2, 0.2, 0.2], buzzes)
        self.assertEqual([0.2, 0.2], waits)

    def test_mission2_escort_matches_mission1_white_pulses(self):
        led = FakeLed()
        buzzes = []
        waits = []
        player = GroundCuePlayer(
            led=led,
            buzzer=buzzes.append,
            wait=waits.append,
        )

        player.play_mission2_escort_acquired()

        self.assertEqual(
            [
                ("solid", (255, 255, 255), 20),
                ("off",),
                ("solid", (255, 255, 255), 20),
                ("off",),
                ("solid", (255, 255, 255), 20),
                ("off",),
            ],
            led.calls,
        )
        self.assertEqual([0.2, 0.2, 0.2], buzzes)
        self.assertEqual([0.2, 0.2], waits)

    def test_mission2_intermediate_cues_are_green_then_off(self):
        for method_name in (
            "play_mission2_target_locked",
            "play_mission2_retakeoff_started",
        ):
            led = FakeLed()
            buzzes = []
            player = GroundCuePlayer(led=led, buzzer=buzzes.append)

            getattr(player, method_name)()

            self.assertEqual(
                [("solid", (0, 255, 0), 20), ("off",)],
                led.calls,
            )
            self.assertEqual([1.0], buzzes)

    def test_mission2_completed_restores_default_flow(self):
        led = FakeLed()
        buzzes = []
        player = GroundCuePlayer(led=led, buzzer=buzzes.append)

        player.play_mission2_completed()

        self.assertEqual(
            [("solid", (0, 255, 0), 20), ("flow",)],
            led.calls,
        )
        self.assertEqual([1.0], buzzes)

    def test_drop_cue_is_one_second_red_then_off(self):
        led = FakeLed()
        buzzes = []
        player = GroundCuePlayer(led=led, buzzer=buzzes.append)

        player.play_mission1_drop()

        self.assertEqual(
            [("solid", (255, 0, 0), 20), ("off",)],
            led.calls,
        )
        self.assertEqual([1.0], buzzes)

    def test_completed_cue_restores_default_flow(self):
        led = FakeLed()
        buzzes = []
        player = GroundCuePlayer(led=led, buzzer=buzzes.append)

        player.play_mission1_completed()

        self.assertEqual(
            [("solid", (0, 255, 0), 20), ("flow",)],
            led.calls,
        )
        self.assertEqual([1.0], buzzes)

    def test_buzzer_failure_keeps_led_duration_and_restores_output(self):
        led = FakeLed()
        waits = []

        def fail_buzzer(_duration):
            raise RuntimeError("buzzer failed")

        player = GroundCuePlayer(
            led=led,
            buzzer=fail_buzzer,
            wait=waits.append,
        )

        player.play_mission1_drop()

        self.assertEqual([1.0], waits)
        self.assertEqual(("off",), led.calls[-1])

    def test_completed_cue_restores_flow_after_buzzer_failure(self):
        led = FakeLed()
        waits = []

        def fail_buzzer(_duration):
            raise RuntimeError("buzzer failed")

        player = GroundCuePlayer(
            led=led,
            buzzer=fail_buzzer,
            wait=waits.append,
        )

        player.play_mission1_completed()

        self.assertEqual([1.0], waits)
        self.assertEqual(("flow",), led.calls[-1])

    def test_mission2_cleanup_survives_output_failures(self):
        waits = []

        def fail_buzzer(_duration):
            raise RuntimeError("buzzer failed")

        target_led = FakeLed()
        player = GroundCuePlayer(
            led=target_led,
            buzzer=fail_buzzer,
            wait=waits.append,
        )
        player.play_mission2_target_locked()
        self.assertEqual(("off",), target_led.calls[-1])

        completed_led = FakeLed(fail="solid")
        player = GroundCuePlayer(
            led=completed_led,
            buzzer=fail_buzzer,
            wait=waits.append,
        )
        player.play_mission2_completed()
        self.assertEqual(("flow",), completed_led.calls[-1])

    def test_led_failures_do_not_escape(self):
        player = GroundCuePlayer(
            led=FakeLed(fail="solid"),
            buzzer=lambda _duration: None,
        )
        player.play_mission1_drop()

        player = GroundCuePlayer(
            led=FakeLed(fail="off"),
            buzzer=lambda _duration: None,
        )
        player.play_mission1_drop()


if __name__ == "__main__":
    unittest.main()
