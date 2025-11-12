from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "script"
SCRIPT_PATH = SCRIPT_DIR / "validate_bv_music.py"


def load_validate_bv_music_module() -> Any:
    if str(SCRIPT_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPT_DIR))
    spec = importlib.util.spec_from_file_location("validate_bv_music", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


validate_bv_music = load_validate_bv_music_module()


def header() -> dict[str, Any]:
    return {
        "startTime": 0,
        "duration": 2,
        "timeUnit": "beats",
        "grid": "1/16_bar",
        "tempoMap": [{"time": 0, "bpm": 120}],
        "timeSignatureMap": [{"time": 0, "numerator": 4, "denominator": 4}],
    }


def melody() -> list[dict[str, Any]]:
    return [
        {"name": "C4", "midi": 60, "time": 0, "duration": 1},
        {"name": "D4", "midi": 62, "time": 1, "duration": 1},
    ]


def tracks() -> dict[str, list[dict[str, Any]]]:
    return {
        "LeadDouble": melody(),
        "HHA": [
            {"name": "E4", "midi": 64, "time": 0, "duration": 1},
            {"name": "F4", "midi": 65, "time": 1, "duration": 1},
        ],
        "HHB": [
            {"name": "G4", "midi": 67, "time": 0, "duration": 1},
            {"name": "A4", "midi": 69, "time": 1, "duration": 1},
        ],
        "HHF": [
            {"name": "C5", "midi": 72, "time": 0, "duration": 1},
            {"name": "C5", "midi": 72, "time": 1, "duration": 1},
        ],
        "LHA": [
            {"name": "E3", "midi": 52, "time": 0, "duration": 1},
            {"name": "F3", "midi": 53, "time": 1, "duration": 1},
        ],
        "LHB": [
            {"name": "G3", "midi": 55, "time": 0, "duration": 1},
            {"name": "A3", "midi": 57, "time": 1, "duration": 1},
        ],
        "LHF": [
            {"name": "C3", "midi": 48, "time": 0, "duration": 1},
            {"name": "C3", "midi": 48, "time": 1, "duration": 1},
        ],
        "VocalPad1": [],
        "VocalPad2": [],
        "VocalPad3": [],
        "VocalPad4": [],
    }


class ValidateBvMusicTest(unittest.TestCase):
    def write_project(self, root: Path, payload_tracks: dict[str, list[dict[str, Any]]]) -> Path:
        (root / "melody.json").write_text(
            json.dumps({"header": header(), "melody": melody()}),
            encoding="utf-8",
        )
        bv_path = root / "bv_raw.json"
        bv_path.write_text(
            json.dumps({"header": header(), "tracks": payload_tracks}),
            encoding="utf-8",
        )
        return bv_path

    def test_passes_valid_arrangement_report(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bv_path = self.write_project(Path(temp_dir), tracks())

            report, exit_code = validate_bv_music.validate(bv_path, min_score=80)

        self.assertEqual(exit_code, 0)
        self.assertTrue(report["passed"])
        self.assertGreaterEqual(report["score"], 80)
        self.assertEqual(report["hardErrors"], [])

    def test_fails_hard_validation_for_extra_note_fields(self) -> None:
        with TemporaryDirectory() as temp_dir:
            payload_tracks = tracks()
            payload_tracks["HHA"][0]["velocity"] = 90
            bv_path = self.write_project(Path(temp_dir), payload_tracks)

            report, exit_code = validate_bv_music.validate(bv_path, min_score=80)

        self.assertEqual(exit_code, 1)
        self.assertFalse(report["passed"])
        self.assertIn("must contain only", report["hardErrors"][0])


if __name__ == "__main__":
    unittest.main()
