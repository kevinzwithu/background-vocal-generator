from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "script" / "chord_json_to_note.py"


def load_module() -> Any:
    spec = importlib.util.spec_from_file_location("chord_json_to_note", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


chord_json_to_note = load_module()


def note(midi: int, time: float) -> dict[str, Any]:
    return {"midi": midi, "time": time, "duration": 1}


def payload(
    chord: list[dict[str, Any]],
    *,
    duration: float = 14,
    tempo_map: list[dict[str, Any]] | None = None,
    time_signature_map: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "header": {
            "startTime": 0,
            "duration": duration,
            "timeUnit": "beats",
            "grid": "1/32_bar",
            "tempoMap": tempo_map or [{"time": 0, "bpm": 120}],
            "timeSignatureMap": time_signature_map
            or [
                {"time": 0, "numerator": 3, "denominator": 4},
                {"time": 6, "numerator": 4, "denominator": 4},
            ],
        },
        "chord": chord,
    }


class ChordJsonToNoteTest(unittest.TestCase):
    def convert(self, source: dict[str, Any]) -> tuple[str, Any]:
        existing = getattr(self, "temp_dir", None)
        if existing is not None:
            existing.cleanup()
        self.temp_dir = TemporaryDirectory()
        root = Path(self.temp_dir.name)
        chord_path = root / "chord.json"
        chord_path.write_text(json.dumps(source), encoding="utf-8")
        result = chord_json_to_note.convert_chord_json_to_note(chord_path)
        return (root / "note.txt").read_text(encoding="utf-8"), result

    def tearDown(self) -> None:
        temp_dir = getattr(self, "temp_dir", None)
        if temp_dir is not None:
            temp_dir.cleanup()

    def test_renders_complete_chart_with_markers_and_multiple_events(self) -> None:
        source = payload(
            [
                note(56, 3),
                note(60, 3),
                note(63, 3),
                note(61, 7.5),
                note(65, 7.5),
                note(68, 7.5),
                note(63, 9),
                note(67, 9),
                note(70, 9),
            ],
            tempo_map=[{"time": 0, "bpm": 120}, {"time": 6, "bpm": 96}],
        )

        text, result = self.convert(source)

        self.assertEqual(result.bar_count, 4)
        self.assertEqual(result.event_count, 3)
        self.assertEqual(result.warnings, ())
        self.assertEqual(
            text,
            "\n".join(
                [
                    "Time Unit: beats",
                    "",
                    "Bar 1: Time Signature 3/4, BPM 120",
                    "Bar 1: beat 1 N.C.",
                    "Bar 2: beat 1 Ab",
                    "",
                    "Bar 3: Time Signature 4/4, BPM 96",
                    "Bar 3: beat 2.5 Db | beat 4 Eb",
                    "Bar 4: beat 1 N.C.",
                    "",
                ]
            ),
        )

    def test_ignores_inversion_and_uses_pop_friendly_flat_names(self) -> None:
        source = payload(
            [note(63, 0), note(56, 0), note(60, 0)],
            duration=3,
            time_signature_map=[{"time": 0, "numerator": 3, "denominator": 4}],
        )

        text, result = self.convert(source)

        self.assertIn("Bar 1: beat 1 Ab", text)
        self.assertEqual(result.warnings, ())

    def test_uses_sharp_spelling_for_diminished_passing_chord(self) -> None:
        source = payload(
            [note(61, 0), note(64, 0), note(67, 0)],
            duration=3,
            time_signature_map=[{"time": 0, "numerator": 3, "denominator": 4}],
        )

        text, result = self.convert(source)

        self.assertIn("Bar 1: beat 1 C#dim", text)
        self.assertEqual(result.warnings, ())

    def test_reports_exact_pitch_class_ambiguity_for_agent_review(self) -> None:
        source = payload(
            [note(60, 0), note(64, 0), note(67, 0), note(69, 0)],
            duration=3,
            time_signature_map=[{"time": 0, "numerator": 3, "denominator": 4}],
        )

        text, result = self.convert(source)

        self.assertIn("Bar 1: beat 1 C6", text)
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("alternatives: Am7", result.warnings[0])

    def test_names_minor_seventh_without_fifth(self) -> None:
        source = payload(
            [note(39, 0), note(41, 0), note(44, 0)],
            duration=3,
            time_signature_map=[{"time": 0, "numerator": 4, "denominator": 4}],
        )

        text, result = self.convert(source)

        self.assertIn("Bar 1: beat 1 Fm7(no5)", text)
        self.assertEqual(result.warnings, ())

    def test_reports_incomplete_triad_for_agent_review(self) -> None:
        source = payload(
            [note(60, 0), note(64, 0)],
            duration=3,
            time_signature_map=[{"time": 0, "numerator": 3, "denominator": 4}],
        )

        text, result = self.convert(source)

        self.assertIn("Bar 1: beat 1 C", text)
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("incomplete or ambiguous", result.warnings[0])

    def test_rejects_mid_bar_tempo_change(self) -> None:
        source = payload(
            [note(60, 0), note(64, 0), note(67, 0)],
            duration=6,
            tempo_map=[{"time": 0, "bpm": 120}, {"time": 2, "bpm": 96}],
            time_signature_map=[{"time": 0, "numerator": 3, "denominator": 4}],
        )

        with self.assertRaisesRegex(
            chord_json_to_note.ChordChartError,
            "BPM change is not aligned to a bar boundary",
        ):
            self.convert(source)

    def test_rejects_non_beats_time_unit(self) -> None:
        source = payload([note(60, 0), note(64, 0), note(67, 0)])
        source["header"]["timeUnit"] = "seconds"

        with self.assertRaisesRegex(chord_json_to_note.ChordChartError, "must be 'beats'"):
            self.convert(source)

    def test_rejects_header_event_count_and_time_signature_size_limits(self) -> None:
        source = payload([note(60, 0), note(64, 0), note(67, 0)])
        with unittest.mock.patch.object(chord_json_to_note, "MAX_TEMPO_EVENTS", 0):
            with self.assertRaisesRegex(chord_json_to_note.ChordChartError, "Tempo event count"):
                self.convert(source)

        source = payload(
            [note(60, 0), note(64, 0), note(67, 0)],
            time_signature_map=[{"time": 0, "numerator": 256, "denominator": 4}],
        )
        with self.assertRaisesRegex(chord_json_to_note.ChordChartError, "numerator must be at most"):
            self.convert(source)

    def test_atomic_no_overwrite_preserves_racing_output(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "note.txt"
            path.write_text("existing", encoding="utf-8")

            with (
                unittest.mock.patch.object(Path, "exists", return_value=False),
                self.assertRaisesRegex(chord_json_to_note.ChordChartError, "already exists"),
            ):
                chord_json_to_note.write_text_atomically(path, "new", overwrite=False)

            self.assertEqual(path.read_text(encoding="utf-8"), "existing")


if __name__ == "__main__":
    unittest.main()
