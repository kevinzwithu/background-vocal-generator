from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch


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
        (root / "note.txt").write_text(
            "\n".join(
                [
                    "Time Unit: beats",
                    "",
                    "Bar 1: Time Signature 4/4, BPM 120",
                    "Bar 1: beat 1 C | beat 2 F",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        bv_path = root / "bv_raw.json"
        bv_path.write_text(
            json.dumps({"header": header(), "tracks": payload_tracks}),
            encoding="utf-8",
        )
        return bv_path

    def write_note_chart(self, path: Path, chord_lines: list[str], marker_lines: list[str]) -> None:
        path.write_text(
            "\n".join(["Time Unit: beats", "", *marker_lines, *chord_lines]) + "\n",
            encoding="utf-8",
        )

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

    def test_fails_when_note_txt_is_missing(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "melody.json").write_text(
                json.dumps({"header": header(), "melody": melody()}),
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
        self.assertIn("Missing required note.txt", report["hardErrors"][0])

    def test_fails_when_source_melody_json_is_missing(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bv_path = root / "bv_raw.json"
            bv_path.write_text(
                json.dumps({"header": header(), "tracks": tracks()}),
                encoding="utf-8",
            )
            self.write_note_chart(
                root / "note.txt",
                ["Bar 1: beat 1 C"],
                ["Bar 1: Time Signature 4/4, BPM 120"],
            )

            report, exit_code = validate_bv_music.validate(bv_path, min_score=80)

        self.assertEqual(exit_code, 1)
        self.assertFalse(report["passed"])
        self.assertIn("Missing required source melody.json or canonical.json", report["hardErrors"][0])

    def test_fails_when_source_json_has_no_melody_list(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bv_path = self.write_project(root, tracks())
            (root / "melody.json").write_text(json.dumps({"header": header()}), encoding="utf-8")

            report, exit_code = validate_bv_music.validate(bv_path, min_score=80)

        self.assertEqual(exit_code, 1)
        self.assertFalse(report["passed"])
        self.assertIn("must contain a melody list", report["hardErrors"][0])

    def test_fails_when_source_melody_or_control_exceeds_limit(self) -> None:
        cases = [
            ("MAX_SOURCE_MELODY_NOTES", "Source melody note count"),
            ("MAX_CONTROL_EVENTS", "Control event count"),
        ]

        for limit_name, message in cases:
            with self.subTest(limit_name=limit_name), TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                bv_path = self.write_project(root, tracks())
                if limit_name == "MAX_CONTROL_EVENTS":
                    (root / "melody.json").write_text(
                        json.dumps(
                            {
                                "header": header(),
                                "melody": melody(),
                                "control": [
                                    {"section": "verse", "time": 0, "duration": 1},
                                    {"section": "chorus", "time": 1, "duration": 1},
                                ],
                            }
                        ),
                        encoding="utf-8",
                    )

                with patch.object(validate_bv_music.bv_raw_to_midi, limit_name, 1):
                    report, exit_code = validate_bv_music.validate(bv_path, min_score=80)

                self.assertEqual(exit_code, 1)
                self.assertFalse(report["passed"])
                self.assertIn(message, report["hardErrors"][0])

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
            (root / "note.txt").write_text(
                "\n".join(
                    [
                        "Time Unit: beats",
                        "",
                        "Bar 1: Time Signature 4/4, BPM 120",
                        "Bar 1: beat 1 C | beat 2 F",
                    ]
                )
                + "\n",
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

    def test_chord_events_respects_tempo_changes(self) -> None:
        tempo_header = header()
        tempo_header["duration"] = 8
        tempo_header["tempoMap"] = [{"time": 0, "bpm": 120}, {"time": 4, "bpm": 96}]

        with TemporaryDirectory() as temp_dir:
            note_txt = Path(temp_dir) / "note.txt"
            self.write_note_chart(
                note_txt,
                ["Bar 1: beat 1 C", "Bar 2: beat 1 F"],
                [
                    "Bar 1: Time Signature 4/4, BPM 120",
                    "Bar 2: BPM 96",
                ],
            )

            events = validate_bv_music.chord_events(note_txt, tempo_header)

        self.assertEqual([(time, chord) for time, _, chord in events], [(0, "C"), (4, "F")])

    def test_chord_events_rejects_invalid_bar_and_beat_positions(self) -> None:
        cases = [
            ("Bar 0: beat 0 C", "bar must be at least 1"),
            ("Bar 1: beat 9 C", "beat 9 is outside bar 1"),
        ]

        for content, message in cases:
            with self.subTest(content=content), TemporaryDirectory() as temp_dir:
                note_txt = Path(temp_dir) / "note.txt"
                self.write_note_chart(
                    note_txt,
                    [content],
                    ["Bar 1: Time Signature 4/4, BPM 120"],
                )

                with self.assertRaisesRegex(ValueError, message):
                    validate_bv_music.chord_events(note_txt, header())

    def test_chord_events_rejects_unknown_chord_names(self) -> None:
        with TemporaryDirectory() as temp_dir:
            note_txt = Path(temp_dir) / "note.txt"
            self.write_note_chart(
                note_txt,
                ["Bar 1: beat 1 TotallyWrong"],
                ["Bar 1: Time Signature 4/4, BPM 120"],
            )

            with self.assertRaisesRegex(ValueError, "Unsupported chord name: TotallyWrong"):
                validate_bv_music.chord_events(note_txt, header())

    def test_chord_events_requires_complete_ordered_bars(self) -> None:
        complete_header = header()
        complete_header["duration"] = 8
        cases = [
            (["Bar 1: beat 1 C"], "complete and ordered"),
            (["Bar 1: beat 1 C", "Bar 1: beat 1 F"], "complete and ordered"),
            (["Bar 2: beat 1 F", "Bar 1: beat 1 C"], "complete and ordered"),
        ]

        for chord_lines, message in cases:
            with self.subTest(chord_lines=chord_lines), TemporaryDirectory() as temp_dir:
                note_txt = Path(temp_dir) / "note.txt"
                self.write_note_chart(
                    note_txt,
                    chord_lines,
                    ["Bar 1: Time Signature 4/4, BPM 120"],
                )

                with self.assertRaisesRegex(ValueError, message):
                    validate_bv_music.chord_events(note_txt, complete_header)

    def test_chord_events_requires_matching_header_markers(self) -> None:
        cases = [
            (
                variable_time_signature_header(),
                ["Bar 1: Time Signature 3/4, BPM 120"],
                "time signature markers",
            ),
            (
                header(),
                ["Bar 1: Time Signature 4/4, BPM 96"],
                "BPM markers",
            ),
        ]

        for chart_header, markers, message in cases:
            segments = validate_bv_music.build_time_signature_segments(chart_header)
            bar_count = validate_bv_music.expected_bar_count(chart_header, segments)
            chord_lines = [f"Bar {bar}: beat 1 C" for bar in range(1, bar_count + 1)]
            with self.subTest(message=message), TemporaryDirectory() as temp_dir:
                note_txt = Path(temp_dir) / "note.txt"
                self.write_note_chart(note_txt, chord_lines, markers)

                with self.assertRaisesRegex(ValueError, message):
                    validate_bv_music.chord_events(note_txt, chart_header)

    def test_chord_pitch_classes_supports_all_declared_qualities(self) -> None:
        expected = {
            "": {0, 4, 7},
            "m": {0, 3, 7},
            "dim": {0, 3, 6},
            "aug": {0, 4, 8},
            "sus": {0, 5, 7},
            "sus2": {0, 2, 7},
            "sus4": {0, 5, 7},
            "add2": {0, 2, 4, 7},
            "add9": {0, 2, 4, 7},
            "6": {0, 4, 7, 9},
            "7": {0, 4, 7, 10},
            "9": {0, 2, 4, 7, 10},
            "11": {0, 2, 4, 5, 7, 10},
            "13": {0, 2, 4, 5, 7, 9, 10},
            "m6": {0, 3, 7, 9},
            "m7": {0, 3, 7, 10},
            "m7b5": {0, 3, 6, 10},
            "m9": {0, 2, 3, 7, 10},
            "m11": {0, 2, 3, 5, 7, 10},
            "m13": {0, 2, 3, 5, 7, 9, 10},
            "maj7": {0, 4, 7, 11},
            "maj9": {0, 2, 4, 7, 11},
            "maj11": {0, 2, 4, 5, 7, 11},
            "maj13": {0, 2, 4, 5, 7, 9, 11},
            "dim7": {0, 3, 6, 9},
            "aug7": {0, 4, 8, 10},
            "7b9": {0, 1, 4, 7, 10},
            "7sus2": {0, 2, 7, 10},
            "7sus4": {0, 5, 7, 10},
        }
        self.assertEqual(set(expected), set(validate_bv_music.CHORD_QUALITY_INTERVALS))

        for quality, pitch_classes in expected.items():
            with self.subTest(quality=quality):
                self.assertEqual(
                    validate_bv_music.chord_pitch_classes(f"C{quality}"),
                    pitch_classes,
                )

    def test_validate_reports_invalid_note_txt_without_crashing(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bv_path = self.write_project(root, tracks())
            self.write_note_chart(
                root / "note.txt",
                ["Bar 1: beat 1 TotallyWrong"],
                ["Bar 1: Time Signature 4/4, BPM 120"],
            )

            report, exit_code = validate_bv_music.validate(bv_path, min_score=80)

        self.assertEqual(exit_code, 1)
        self.assertFalse(report["passed"])
        self.assertIn("Unsupported chord name", report["hardErrors"][0])

    def test_validate_rejects_incomplete_note_txt(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            complete_header = header()
            complete_header["duration"] = 8
            (root / "melody.json").write_text(
                json.dumps({"header": complete_header, "melody": melody()}),
                encoding="utf-8",
            )
            bv_path = root / "bv_raw.json"
            bv_path.write_text(
                json.dumps({"header": complete_header, "tracks": tracks()}),
                encoding="utf-8",
            )
            self.write_note_chart(
                root / "note.txt",
                ["Bar 1: beat 1 C"],
                ["Bar 1: Time Signature 4/4, BPM 120"],
            )

            report, exit_code = validate_bv_music.validate(bv_path, min_score=80)

        self.assertEqual(exit_code, 1)
        self.assertFalse(report["passed"])
        self.assertIn("complete and ordered", report["hardErrors"][0])


if __name__ == "__main__":
    unittest.main()
