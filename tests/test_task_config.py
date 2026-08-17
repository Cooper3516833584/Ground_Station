import json
from pathlib import Path
import unittest

from components.models import CommandId
from components.screen_commands import ScreenCommandDetector
from components.task_config import load_task_settings


ROOT = Path(__file__).resolve().parents[1]
SCRATCH = ROOT / ".test_tmp"


class TaskConfigTests(unittest.TestCase):
    def tearDown(self) -> None:
        for leftover in SCRATCH.glob("task_*.json"):
            try:
                leftover.unlink()
            except OSError:
                pass

    def _write_config(self, config: dict, name: str) -> Path:
        SCRATCH.mkdir(parents=True, exist_ok=True)
        path = SCRATCH / name
        path.write_text(json.dumps(config), encoding="utf-8")
        return path

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
        path = self._write_config(config, "task_invalid_command.json")
        with self.assertRaisesRegex(ValueError, "must be one of"):
            load_task_settings(path)

    def test_cooldown_comes_from_top_level_and_legacy_serial(self):
        config = json.loads((ROOT / "task_config.json").read_text(encoding="utf-8"))
        del config["cooldown_seconds"]
        path = self._write_config(config, "task_legacy_cooldown.json")
        settings = load_task_settings(path)
        self.assertEqual(settings.cooldown_seconds, 0.75)

        config["cooldown_seconds"] = 1.5
        path = self._write_config(config, "task_top_cooldown.json")
        settings = load_task_settings(path)
        self.assertEqual(settings.cooldown_seconds, 1.5)

    def test_repository_config_has_no_machine_serial(self):
        settings = load_task_settings(ROOT / "task_config.json")
        self.assertEqual(settings.cooldown_seconds, 0.75)
        self.assertFalse(hasattr(settings, "serial"))


if __name__ == "__main__":
    unittest.main()
