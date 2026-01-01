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


def variable_time_signature_header() -> dict[str, Any]:
    return {
        "startTime": 0,
        "duration": 14,
        "timeUnit": "beats",
        "grid": "1/16_bar",
        "tempoMap": [{"time": 0, "bpm": 120}],
        "timeSignatureMap": [
            {"time": 0, "numerator": 3, "denominator": 4},
            {"time": 6, "numerator": 4, "denominator": 4},
        ],
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

    def test_reports_soft_scoring_side_input_errors_without_crashing(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "melody.json").write_text(
                json.dumps(
                    {
                        "header": header(),
                        "melody": melody(),
                        "control": [{"section": "verse", "time": "bad", "duration": 1}],
                    }
                ),
                encoding="utf-8",
            )
            bv_path = root / "bv_raw.json"
            bv_path.write_text(
                json.dumps({"header": header(), "tracks": tracks()}),
                encoding="utf-8",
            )

            report, exit_code = validate_bv_music.validate(bv_path, min_score=80)

        self.assertEqual(exit_code, 1)
        self.assertFalse(report["passed"])
        self.assertIn("Unable to score arrangement", report["hardErrors"][0])

    def test_bar_key_respects_time_signature_changes(self) -> None:
        segments = validate_bv_music.build_time_signature_segments(
            variable_time_signature_header()
        )

        self.assertEqual(validate_bv_music.bar_key(0, segments), 1)
        self.assertEqual(validate_bv_music.bar_key(3, segments), 2)
        self.assertEqual(validate_bv_music.bar_key(6, segments), 3)
        self.assertEqual(validate_bv_music.bar_key(9.5, segments), 3)
        self.assertEqual(validate_bv_music.bar_key(10, segments), 4)

    def test_chord_events_respects_time_signature_changes(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            note_txt = root / "note.txt"
            note_txt.write_text(
                "\n".join(
                    [
                        "Time Unit: beats",
                        "",
                        "Bar 1: Time Signature 3/4, BPM 120",
                        "Bar 1: beat 1 C",
                        "Bar 2: beat 1 G",
                        "Bar 3: Time Signature 4/4",
                        "Bar 3: beat 1 D",
                        "Bar 4: beat 1 F",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            events = validate_bv_music.chord_events(
                note_txt,
                variable_time_signature_header(),
            )

        self.assertEqual(
            [(time, chord) for time, _, chord in events],
            [(0, "C"), (3, "G"), (6, "D"), (10, "F")],
        )


if __name__ == "__main__":
    unittest.main()
