from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any


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
    "HighHarmonyA",
    "HighHarmonyB",
    "LowHarmony",
    "VocalPad1",
    "VocalPad2",
    "VocalPad3",
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
    payload["HighHarmonyA"] = [{"name": "E4", "midi": 64, "time": 0, "duration": 2}]
    payload["HighHarmonyB"] = [{"name": "G4", "midi": 67, "time": 0, "duration": 2}]
    payload["LowHarmony"] = [{"name": "C3", "midi": 48, "time": 0, "duration": 2}]
    payload["VocalPad1"] = [{"name": "C4", "midi": 60, "time": 0, "duration": 4}]
    payload["VocalPad2"] = [{"name": "E4", "midi": 64, "time": 0, "duration": 4}]
    payload["VocalPad3"] = [{"name": "G4", "midi": 67, "time": 0, "duration": 4}]
    return payload


class BvRawToMidiTest(unittest.TestCase):
    def test_writes_format_1_midi_with_conductor_and_seven_vocal_tracks(self) -> None:
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
        self.assertEqual(int.from_bytes(data[10:12], "big"), 8)
        self.assertEqual(int.from_bytes(data[12:14], "big"), 480)

    def test_uses_sibling_melody_header_when_bv_header_is_missing(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "melody.json").write_text(
                json.dumps({"header": header(), "melody": []}),
                encoding="utf-8",
            )
            bv_path = root / "bv_raw.json"
            bv_path.write_text(json.dumps({"tracks": tracks()}), encoding="utf-8")

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


if __name__ == "__main__":
    unittest.main()
