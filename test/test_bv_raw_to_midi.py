from __future__ import annotations

import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "script" / "bv_raw_to_midi.py"


def load_bv_raw_to_midi_module() -> Any:
    spec = importlib.util.spec_from_file_location("bv_raw_to_midi", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bv_raw_to_midi = load_bv_raw_to_midi_module()


EXPECTED_TRACKS = (
    "LeadDouble",
    "HHA",
    "HHB",
    "HHF",
    "LHA",
    "LHB",
    "LHF",
    "VocalPad1",
    "VocalPad2",
    "VocalPad3",
    "VocalPad4",
)


def header() -> dict[str, Any]:
    return {
        "startTime": 0,
        "duration": 4,
        "timeUnit": "beats",
        "grid": "1/16_bar",
        "tempoMap": [{"time": 0, "bpm": 120}, {"time": 2, "bpm": 96}],
        "timeSignatureMap": [{"time": 0, "numerator": 4, "denominator": 4}],
    }


def tracks() -> dict[str, list[dict[str, Any]]]:
    payload = {name: [] for name in EXPECTED_TRACKS}
    payload["LeadDouble"] = [
        {"name": "C4", "midi": 60, "time": 0, "duration": 1},
        {"name": "D4", "midi": 62, "time": 1, "duration": 1},
    ]
    payload["HHA"] = [{"name": "E4", "midi": 64, "time": 0, "duration": 2}]
    payload["HHB"] = [{"name": "G4", "midi": 67, "time": 0, "duration": 2}]
    payload["HHF"] = [{"name": "C5", "midi": 72, "time": 0, "duration": 2}]
    payload["LHA"] = [{"name": "E3", "midi": 52, "time": 0, "duration": 2}]
    payload["LHB"] = [{"name": "G3", "midi": 55, "time": 0, "duration": 2}]
    payload["LHF"] = [{"name": "C3", "midi": 48, "time": 0, "duration": 2}]
    payload["VocalPad1"] = [{"name": "C4", "midi": 60, "time": 0, "duration": 4}]
    payload["VocalPad2"] = [{"name": "E4", "midi": 64, "time": 0, "duration": 4}]
    payload["VocalPad3"] = [{"name": "G4", "midi": 67, "time": 0, "duration": 4}]
    payload["VocalPad4"] = [{"name": "C5", "midi": 72, "time": 0, "duration": 4}]
    return payload


def melody() -> list[dict[str, Any]]:
    return [
        {"name": "C4", "midi": 60, "time": 0, "duration": 1},
        {"name": "D4", "midi": 62, "time": 1, "duration": 1},
    ]


def melody_aligned_tracks() -> dict[str, list[dict[str, Any]]]:
    payload = {name: [] for name in EXPECTED_TRACKS}
    payload["LeadDouble"] = melody()
    payload["HHA"] = [
        {"name": "E4", "midi": 64, "time": 0, "duration": 1},
        {"name": "F4", "midi": 65, "time": 1, "duration": 1},
    ]
    payload["HHB"] = [
        {"name": "G4", "midi": 67, "time": 0, "duration": 1},
        {"name": "A4", "midi": 69, "time": 1, "duration": 1},
    ]
    payload["HHF"] = [
        {"name": "C5", "midi": 72, "time": 0, "duration": 1},
        {"name": "C5", "midi": 72, "time": 1, "duration": 1},
    ]
    payload["LHA"] = [
        {"name": "E3", "midi": 52, "time": 0, "duration": 1},
        {"name": "F3", "midi": 53, "time": 1, "duration": 1},
    ]
    payload["LHB"] = [
        {"name": "G3", "midi": 55, "time": 0, "duration": 1},
        {"name": "A3", "midi": 57, "time": 1, "duration": 1},
    ]
    payload["LHF"] = [
        {"name": "C3", "midi": 48, "time": 0, "duration": 1},
        {"name": "C3", "midi": 48, "time": 1, "duration": 1},
    ]
    payload["VocalPad1"] = [{"name": "C4", "midi": 60, "time": 0, "duration": 4}]
    payload["VocalPad2"] = [{"name": "E4", "midi": 64, "time": 0, "duration": 4}]
    payload["VocalPad3"] = [{"name": "G4", "midi": 67, "time": 0, "duration": 4}]
    payload["VocalPad4"] = [{"name": "C5", "midi": 72, "time": 0, "duration": 4}]
    return payload


class BvRawToMidiTest(unittest.TestCase):
    def test_writes_format_1_midi_with_conductor_and_eleven_vocal_tracks(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bv_path = root / "bv_raw.json"
            bv_path.write_text(
                json.dumps({"header": header(), "tracks": tracks()}),
                encoding="utf-8",
            )

            output_path = bv_raw_to_midi.convert_bv_raw_to_midi(bv_path)
            data = output_path.read_bytes()

        self.assertEqual(data[:4], b"MThd")
        self.assertEqual(int.from_bytes(data[8:10], "big"), 1)
        self.assertEqual(int.from_bytes(data[10:12], "big"), 12)
        self.assertEqual(int.from_bytes(data[12:14], "big"), 480)

    def test_uses_sibling_melody_header_when_bv_header_is_missing(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "melody.json").write_text(
                json.dumps({"header": header(), "melody": melody()}),
                encoding="utf-8",
            )
            bv_path = root / "bv_raw.json"
            bv_path.write_text(json.dumps({"tracks": melody_aligned_tracks()}), encoding="utf-8")

            output_path = bv_raw_to_midi.convert_bv_raw_to_midi(bv_path)
            self.assertTrue(output_path.exists())

    def test_rejects_missing_required_track(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            broken_tracks = tracks()
            broken_tracks.pop("VocalPad3")
            bv_path = root / "bv_raw.json"
            bv_path.write_text(
                json.dumps({"header": header(), "tracks": broken_tracks}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(bv_raw_to_midi.BvMidiError, "Missing BV track"):
                bv_raw_to_midi.convert_bv_raw_to_midi(bv_path)

    def test_rejects_incomplete_harmony_when_source_melody_exists(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "melody.json").write_text(
                json.dumps({"header": header(), "melody": melody()}),
                encoding="utf-8",
            )
            broken_tracks = melody_aligned_tracks()
            broken_tracks["HHA"] = broken_tracks["HHA"][:1]
            bv_path = root / "bv_raw.json"
            bv_path.write_text(
                json.dumps({"header": header(), "tracks": broken_tracks}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(bv_raw_to_midi.BvMidiError, "harmony-enabled melody note"):
                bv_raw_to_midi.convert_bv_raw_to_midi(bv_path)

    def test_allows_harmony_omissions_inside_no_bv_control_span(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "melody.json").write_text(
                json.dumps(
                    {
                        "header": header(),
                        "melody": melody(),
                        "control": [
                            {
                                "section": "no_bv",
                                "name": "E-1",
                                "midi": 4,
                                "time": 1,
                                "duration": 1,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            payload_tracks = melody_aligned_tracks()
            for track_name in ("HHA", "HHB", "HHF", "LHA", "LHB", "LHF"):
                payload_tracks[track_name] = payload_tracks[track_name][:1]
            bv_path = root / "bv_raw.json"
            bv_path.write_text(
                json.dumps({"header": header(), "tracks": payload_tracks}),
                encoding="utf-8",
            )

            output_path = bv_raw_to_midi.convert_bv_raw_to_midi(bv_path)

            self.assertTrue(output_path.exists())

    def test_rejects_missing_input_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            missing_path = Path(temp_dir) / "bv_raw.json"

            with self.assertRaisesRegex(bv_raw_to_midi.BvMidiError, "does not exist"):
                bv_raw_to_midi.convert_bv_raw_to_midi(missing_path)

    def test_rejects_invalid_input_format(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bv_path = root / "bv_raw.json"
            broken_header = header()
            broken_header["tempoMap"] = [{"time": 0}]
            bv_path.write_text(
                json.dumps({"header": broken_header, "tracks": tracks()}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(bv_raw_to_midi.BvMidiError, "tempoMap\\[0\\] is missing 'bpm'"):
                bv_raw_to_midi.convert_bv_raw_to_midi(bv_path)

    def test_accepts_quarter_bpm_values(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bv_path = root / "bv_raw.json"
            fractional_header = header()
            fractional_header["tempoMap"] = [
                {"time": 0, "bpm": 130.25},
                {"time": 2, "bpm": 130.75},
            ]
            bv_path.write_text(
                json.dumps({"header": fractional_header, "tracks": tracks()}),
                encoding="utf-8",
            )

            output_path = bv_raw_to_midi.convert_bv_raw_to_midi(bv_path)

            self.assertTrue(output_path.exists())

    def test_rejects_bpm_values_finer_than_quarter(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bv_path = root / "bv_raw.json"
            broken_header = header()
            broken_header["tempoMap"] = [{"time": 0, "bpm": 130.125}]
            bv_path.write_text(
                json.dumps({"header": broken_header, "tracks": tracks()}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(bv_raw_to_midi.BvMidiError, "0.25 BPM increments"):
                bv_raw_to_midi.convert_bv_raw_to_midi(bv_path)

    def test_rejects_time_signature_change_off_previous_bar_boundary(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bv_path = root / "bv_raw.json"
            broken_header = header()
            broken_header["duration"] = 8
            broken_header["timeSignatureMap"] = [
                {"time": 0, "numerator": 4, "denominator": 4},
                {"time": 5, "numerator": 3, "denominator": 4},
            ]
            bv_path.write_text(
                json.dumps({"header": broken_header, "tracks": tracks()}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(bv_raw_to_midi.BvMidiError, "previous bar boundary"):
                bv_raw_to_midi.convert_bv_raw_to_midi(bv_path)

    def test_rejects_fractional_midi_without_truncating(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            broken_tracks = tracks()
            broken_tracks["LeadDouble"][0]["midi"] = 60.9
            bv_path = root / "bv_raw.json"
            bv_path.write_text(
                json.dumps({"header": header(), "tracks": broken_tracks}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(bv_raw_to_midi.BvMidiError, "midi must be an integer"):
                bv_raw_to_midi.convert_bv_raw_to_midi(bv_path)

    def test_rejects_invalid_note_field_format(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            broken_tracks = tracks()
            broken_tracks["LeadDouble"][0]["midi"] = "not-a-midi"
            bv_path = root / "bv_raw.json"
            bv_path.write_text(
                json.dumps({"header": header(), "tracks": broken_tracks}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(bv_raw_to_midi.BvMidiError, "midi must be an integer"):
                bv_raw_to_midi.convert_bv_raw_to_midi(bv_path)

    def test_rejects_invalid_source_melody_format(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "melody.json").write_text(
                json.dumps({"header": header(), "melody": [{"name": "C4"}]}),
                encoding="utf-8",
            )
            bv_path = root / "bv_raw.json"
            bv_path.write_text(
                json.dumps({"header": header(), "tracks": tracks()}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(bv_raw_to_midi.BvMidiError, "Source melody note"):
                bv_raw_to_midi.convert_bv_raw_to_midi(bv_path)

    def test_rejects_existing_output_when_overwrite_is_false(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bv_path = root / "bv_raw.json"
            output_path = root / "custom.mid"
            bv_path.write_text(
                json.dumps({"header": header(), "tracks": tracks()}),
                encoding="utf-8",
            )
            output_path.write_bytes(b"existing")

            with self.assertRaisesRegex(bv_raw_to_midi.BvMidiError, "already exists"):
                bv_raw_to_midi.convert_bv_raw_to_midi(
                    bv_path,
                    output_path=output_path,
                    overwrite=False,
                )
            self.assertEqual(output_path.read_bytes(), b"existing")

    def test_main_cancels_when_output_exists_and_user_does_not_confirm(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bv_path = root / "bv_raw.json"
            output_path = root / "bv.mid"
            bv_path.write_text(
                json.dumps({"header": header(), "tracks": tracks()}),
                encoding="utf-8",
            )
            output_path.write_bytes(b"existing")

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                patch("builtins.input", return_value="n"),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                result = bv_raw_to_midi.main([str(bv_path)])

            self.assertEqual(result, 2)
            self.assertEqual(output_path.read_bytes(), b"existing")
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("Canceled", stderr.getvalue())

    def test_main_overwrites_existing_output_after_y_confirmation(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bv_path = root / "bv_raw.json"
            output_path = root / "bv.mid"
            bv_path.write_text(
                json.dumps({"header": header(), "tracks": tracks()}),
                encoding="utf-8",
            )
            output_path.write_bytes(b"existing")

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                patch("builtins.input", return_value="y"),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                result = bv_raw_to_midi.main([str(bv_path)])

            self.assertEqual(result, 0)
            self.assertNotEqual(output_path.read_bytes(), b"existing")
            self.assertIn("Wrote MIDI", stdout.getvalue())
            self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
