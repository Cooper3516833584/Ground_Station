import json
from pathlib import Path
import tempfile
import unittest

from components.models import CommandId
from components.screen_commands import ScreenCommandDetector
from components.task_config import load_task_settings


ROOT = Path(__file__).resolve().parents[1]


class TaskConfigTests(unittest.TestCase):
    def test_repository_config_selects_and_builds_commands(self):
        settings = load_task_settings(ROOT / "task_config.json")
        self.assertEqual(settings.name, "flight_mission")
        self.assertEqual(
            [action.command.command_id for action in settings.actions],
            [CommandId.START_MISSION, CommandId.STOP_MISSION],
        )

        vision = load_task_settings(ROOT / "task_config.json", "vision_acquire")
        self.assertEqual(vision.actions[0].token, "SCAN")
        self.assertEqual(
            vision.actions[0].command.command_id,
            CommandId.START_VISION_ACQUIRE,
        )

    def test_detector_handles_fragmented_and_multiple_tokens(self):
        detector = ScreenCommandDetector(("START", "STOP"))
        self.assertEqual(detector.feed(b"noise-st"), [])
        self.assertEqual(detector.feed(b"artSTOPst"), ["START", "STOP"])
        self.assertEqual(detector.feed(b"art"), ["START"])

    def test_invalid_command_is_rejected(self):
        config = json.loads((ROOT / "task_config.json").read_text(encoding="utf-8"))
        config["tasks"]["flight_mission"]["screen_commands"]["START"][
            "aircraft_command"
        ]["name"] = "RAW_TAKEOFF_BYTES"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "task_config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must be one of"):
                load_task_settings(path)


if __name__ == "__main__":
    unittest.main()
