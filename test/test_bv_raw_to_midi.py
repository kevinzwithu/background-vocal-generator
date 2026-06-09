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
MIDI_TO_JSON_SCRIPT_PATH = ROOT / "script" / "midi_to_json.py"


def load_bv_raw_to_midi_module() -> Any:
    spec = importlib.util.spec_from_file_location("bv_raw_to_midi", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bv_raw_to_midi = load_bv_raw_to_midi_module()


def load_midi_to_json_module() -> Any:
    spec = importlib.util.spec_from_file_location("bv_test_midi_to_json", MIDI_TO_JSON_SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {MIDI_TO_JSON_SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


midi_to_json = load_midi_to_json_module()


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
        "grid": "1/32_bar",
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

            output_path = bv_raw_to_midi.convert_bv_raw_to_midi(
                bv_path, allow_standalone=True
            )
            data = output_path.read_bytes()
            ticks_per_beat, parsed_tracks, _, _ = midi_to_json.parse_midi(output_path)

        self.assertEqual(data[:4], b"MThd")
        self.assertEqual(int.from_bytes(data[8:10], "big"), 1)
        self.assertEqual(int.from_bytes(data[10:12], "big"), 12)
        self.assertEqual(int.from_bytes(data[12:14], "big"), 480)
        self.assertEqual(ticks_per_beat, 480)
        self.assertEqual(len(parsed_tracks), 12)

    def test_uses_sibling_melody_header_when_bv_header_is_missing(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "melody.json").write_text(
                json.dumps({"header": header(), "melody": melody()}),
                encoding="utf-8",
            )
            bv_path = root / "bv_raw.json"
            bv_path.write_text(json.dumps({"tracks": melody_aligned_tracks()}), encoding="utf-8")

            output_path = bv_raw_to_midi.convert_bv_raw_to_midi(
                bv_path, allow_standalone=True
            )
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
                bv_raw_to_midi.convert_bv_raw_to_midi(bv_path, allow_standalone=True)

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

    def test_allows_uniform_octave_transposition_of_lead_double(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "melody.json").write_text(
                json.dumps({"header": header(), "melody": melody()}),
                encoding="utf-8",
            )
            payload_tracks = melody_aligned_tracks()
            for track_name in (
                "LeadDouble",
                "HHA",
                "HHB",
                "HHF",
                "LHA",
                "LHB",
                "LHF",
            ):
                for note in payload_tracks[track_name]:
                    note["midi"] += 12
            bv_path = root / "bv_raw.json"
            bv_path.write_text(
                json.dumps({"header": header(), "tracks": payload_tracks}),
                encoding="utf-8",
            )

            output_path = bv_raw_to_midi.convert_bv_raw_to_midi(
                bv_path, allow_standalone=True
            )

            self.assertTrue(output_path.exists())

    def test_rejects_non_octave_lead_double_transposition(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "melody.json").write_text(
                json.dumps({"header": header(), "melody": melody()}),
                encoding="utf-8",
            )
            payload_tracks = melody_aligned_tracks()
            payload_tracks["LeadDouble"][0]["midi"] += 7
            bv_path = root / "bv_raw.json"
            bv_path.write_text(
                json.dumps({"header": header(), "tracks": payload_tracks}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(bv_raw_to_midi.BvMidiError, "whole-octave"):
                bv_raw_to_midi.convert_bv_raw_to_midi(bv_path, allow_standalone=True)

    def test_rejects_mixed_octave_lead_double_transposition(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "melody.json").write_text(
                json.dumps({"header": header(), "melody": melody()}),
                encoding="utf-8",
            )
            payload_tracks = melody_aligned_tracks()
            payload_tracks["LeadDouble"][0]["midi"] += 12
            bv_path = root / "bv_raw.json"
            bv_path.write_text(
                json.dumps({"header": header(), "tracks": payload_tracks}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(bv_raw_to_midi.BvMidiError, "one uniform octave shift"):
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
            for track_name in ("VocalPad1", "VocalPad2", "VocalPad3", "VocalPad4"):
                payload_tracks[track_name] = []
            bv_path = root / "bv_raw.json"
            bv_path.write_text(
                json.dumps({"header": header(), "tracks": payload_tracks}),
                encoding="utf-8",
            )

            output_path = bv_raw_to_midi.convert_bv_raw_to_midi(bv_path)

            self.assertTrue(output_path.exists())

    def test_rejects_pad_onset_or_sustain_inside_no_bv(self) -> None:
        for pad_time, pad_duration in ((1, 0.5), (0, 1.5)):
            with self.subTest(time=pad_time), TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                (root / "melody.json").write_text(
                    json.dumps(
                        {
                            "header": header(),
                            "melody": melody(),
                            "control": [
                                {"section": "no_bv", "time": 1, "duration": 1}
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                payload_tracks = melody_aligned_tracks()
                for track_name in ("HHA", "HHB", "HHF", "LHA", "LHB", "LHF"):
                    payload_tracks[track_name] = payload_tracks[track_name][:1]
                payload_tracks["VocalPad1"] = [
                    {
                        "name": "C4",
                        "midi": 60,
                        "time": pad_time,
                        "duration": pad_duration,
                    }
                ]
                for track_name in ("VocalPad2", "VocalPad3", "VocalPad4"):
                    payload_tracks[track_name] = []
                bv_path = root / "bv_raw.json"
                bv_path.write_text(
                    json.dumps({"header": header(), "tracks": payload_tracks}),
                    encoding="utf-8",
                )

                with self.assertRaisesRegex(bv_raw_to_midi.BvMidiError, "overlaps no_bv"):
                    bv_raw_to_midi.convert_bv_raw_to_midi(bv_path)

    def test_uses_public_c0_to_c6_range_for_every_bv_track(self) -> None:
        for midi, passes in ((84, True), (85, False)):
            with self.subTest(midi=midi), TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                payload_tracks = tracks()
                payload_tracks["VocalPad1"] = [
                    {
                        "name": "C6" if midi == 84 else "C#6",
                        "midi": midi,
                        "time": 0,
                        "duration": 1,
                    }
                ]
                bv_path = root / "bv_raw.json"
                bv_path.write_text(
                    json.dumps({"header": header(), "tracks": payload_tracks}),
                    encoding="utf-8",
                )

                if passes:
                    output_path = bv_raw_to_midi.convert_bv_raw_to_midi(
                        bv_path, allow_standalone=True
                    )
                    self.assertTrue(output_path.exists())
                else:
                    with self.assertRaisesRegex(
                        bv_raw_to_midi.BvMidiError, "outside the allowed range"
                    ):
                        bv_raw_to_midi.convert_bv_raw_to_midi(
                            bv_path, allow_standalone=True
                        )

    def test_rejects_missing_input_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            missing_path = Path(temp_dir) / "bv_raw.json"

            with self.assertRaisesRegex(bv_raw_to_midi.BvMidiError, "does not exist"):
                bv_raw_to_midi.convert_bv_raw_to_midi(missing_path)

    def test_rejects_json_file_larger_than_limit(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "too-large.json"
            path.write_text("{}", encoding="utf-8")

            with (
                patch.object(bv_raw_to_midi, "MAX_JSON_FILE_BYTES", 1),
                self.assertRaisesRegex(bv_raw_to_midi.BvMidiError, "JSON file size exceeds limit"),
            ):
                bv_raw_to_midi.read_json(path)

    def test_rejects_invalid_utf8_and_extreme_json_integer(self) -> None:
        cases = [
            (b"\xff", "must use UTF-8"),
            (b'{"value": ' + b"9" * 5000 + b"}", "Invalid JSON file"),
        ]

        for raw, message in cases:
            with self.subTest(message=message), TemporaryDirectory() as temp_dir:
                path = Path(temp_dir) / "invalid.json"
                path.write_bytes(raw)

                with self.assertRaisesRegex(bv_raw_to_midi.BvMidiError, message):
                    bv_raw_to_midi.read_json(path)

    def test_rejects_extreme_numeric_string_without_crashing(self) -> None:
        with self.assertRaisesRegex(bv_raw_to_midi.BvMidiError, "must be an integer"):
            bv_raw_to_midi.require_int(
                {"midi": "9" * 5000},
                "midi",
                "note",
            )

    def test_rejects_numeric_value_that_overflows_float_conversion(self) -> None:
        with self.assertRaisesRegex(bv_raw_to_midi.BvMidiError, "must be a number"):
            bv_raw_to_midi.require_number(
                {"duration": 10**400},
                "duration",
                "header",
            )

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

            output_path = bv_raw_to_midi.convert_bv_raw_to_midi(
                bv_path, allow_standalone=True
            )

            self.assertTrue(output_path.exists())

    def test_rejects_tolerance_values_that_can_bypass_validation(self) -> None:
        with self.assertRaisesRegex(bv_raw_to_midi.BvMidiError, "tolerance must be between"):
            bv_raw_to_midi.validate_export_options(
                480,
                80,
                bv_raw_to_midi.MAX_TOLERANCE + 0.001,
            )

    def test_atomic_no_overwrite_preserves_racing_output(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "output.mid"
            path.write_bytes(b"existing")

            with (
                patch.object(Path, "exists", return_value=False),
                self.assertRaisesRegex(bv_raw_to_midi.BvMidiError, "already exists"),
            ):
                bv_raw_to_midi.write_bytes_atomically(path, b"new", overwrite=False)

            self.assertEqual(path.read_bytes(), b"existing")

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

    def test_rejects_bpm_that_cannot_be_encoded_as_midi_tempo(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bv_path = root / "bv_raw.json"
            broken_header = header()
            broken_header["tempoMap"] = [{"time": 0, "bpm": 0.25}]
            bv_path.write_text(
                json.dumps({"header": broken_header, "tracks": tracks()}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(bv_raw_to_midi.BvMidiError, "cannot be represented"):
                bv_raw_to_midi.convert_bv_raw_to_midi(bv_path)

    def test_rejects_header_maps_that_do_not_start_at_zero(self) -> None:
        cases = [
            ("tempoMap", [{"time": 1, "bpm": 120}]),
            ("timeSignatureMap", [{"time": 1, "numerator": 4, "denominator": 4}]),
        ]

        for field, value in cases:
            with self.subTest(field=field), TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                bv_path = root / "bv_raw.json"
                broken_header = header()
                broken_header[field] = value
                bv_path.write_text(
                    json.dumps({"header": broken_header, "tracks": tracks()}),
                    encoding="utf-8",
                )

                with self.assertRaisesRegex(bv_raw_to_midi.BvMidiError, "must start at time 0"):
                    bv_raw_to_midi.convert_bv_raw_to_midi(bv_path, allow_standalone=True)

    def test_rejects_invalid_header_start_and_duration(self) -> None:
        cases = [
            ("startTime", 1, "header.startTime must be 0"),
            ("duration", -1, "header.duration cannot be negative"),
            ("duration", "bad", "header.duration must be a number"),
        ]

        for field, value, message in cases:
            with self.subTest(field=field, value=value), TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                bv_path = root / "bv_raw.json"
                broken_header = header()
                broken_header[field] = value
                bv_path.write_text(
                    json.dumps({"header": broken_header, "tracks": tracks()}),
                    encoding="utf-8",
                )

                with self.assertRaisesRegex(bv_raw_to_midi.BvMidiError, message):
                    bv_raw_to_midi.convert_bv_raw_to_midi(
                        bv_path, allow_standalone=True
                    )

    def test_rejects_time_signature_values_that_do_not_fit_midi_bytes(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bv_path = root / "bv_raw.json"
            broken_header = header()
            broken_header["timeSignatureMap"] = [
                {"time": 0, "numerator": 256, "denominator": 4}
            ]
            bv_path.write_text(
                json.dumps({"header": broken_header, "tracks": tracks()}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(bv_raw_to_midi.BvMidiError, "numerator must be at most 255"):
                bv_raw_to_midi.convert_bv_raw_to_midi(bv_path, allow_standalone=True)

    def test_rejects_header_event_count_limits(self) -> None:
        cases = [
            (
                "tempoMap",
                [{"time": 0, "bpm": 120}, {"time": 1, "bpm": 120}],
                "MAX_TEMPO_EVENTS",
                "Tempo event count exceeds limit",
            ),
            (
                "timeSignatureMap",
                [
                    {"time": 0, "numerator": 4, "denominator": 4},
                    {"time": 4, "numerator": 4, "denominator": 4},
                ],
                "MAX_TIME_SIGNATURE_EVENTS",
                "Time signature event count exceeds limit",
            ),
        ]

        for field, values, limit_name, message in cases:
            with self.subTest(field=field), TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                bv_path = root / "bv_raw.json"
                broken_header = header()
                broken_header[field] = values
                bv_path.write_text(
                    json.dumps({"header": broken_header, "tracks": tracks()}),
                    encoding="utf-8",
                )

                with (
                    patch.object(bv_raw_to_midi, limit_name, 1),
                    self.assertRaisesRegex(bv_raw_to_midi.BvMidiError, message),
                ):
                    bv_raw_to_midi.convert_bv_raw_to_midi(bv_path)

    def test_rejects_per_track_and_global_note_count_limits(self) -> None:
        cases = [
            ("MAX_NOTES_PER_TRACK", 1, "Track LeadDouble note count exceeds limit"),
            ("MAX_TOTAL_NOTES", 1, "Total BV note count exceeds limit"),
        ]

        for limit_name, limit, message in cases:
            with self.subTest(limit_name=limit_name), TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                bv_path = root / "bv_raw.json"
                bv_path.write_text(
                    json.dumps({"header": header(), "tracks": tracks()}),
                    encoding="utf-8",
                )

                with (
                    patch.object(bv_raw_to_midi, limit_name, limit),
                    self.assertRaisesRegex(bv_raw_to_midi.BvMidiError, message),
                ):
                    bv_raw_to_midi.convert_bv_raw_to_midi(bv_path)

    def test_rejects_source_melody_and_control_count_limits(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "melody.json"
            source_path.write_text(
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
            bv_path = root / "bv_raw.json"

            with (
                patch.object(bv_raw_to_midi, "MAX_SOURCE_MELODY_NOTES", 1),
                self.assertRaisesRegex(bv_raw_to_midi.BvMidiError, "Source melody note count"),
            ):
                bv_raw_to_midi.load_source_json(root)

            with (
                patch.object(bv_raw_to_midi, "MAX_CONTROL_EVENTS", 1),
                self.assertRaisesRegex(bv_raw_to_midi.BvMidiError, "Control event count"),
            ):
                bv_raw_to_midi.load_source_json(root)

    def test_rejects_ticks_per_beat_that_sets_smpte_division_bit(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bv_path = root / "bv_raw.json"
            bv_path.write_text(
                json.dumps({"header": header(), "tracks": tracks()}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(bv_raw_to_midi.BvMidiError, "between 1 and 32767"):
                bv_raw_to_midi.convert_bv_raw_to_midi(bv_path, ticks_per_beat=65536)

    def test_rejects_header_duration_shorter_than_latest_note_end(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bv_path = root / "bv_raw.json"
            short_header = header()
            short_header["duration"] = 1
            bv_path.write_text(
                json.dumps({"header": short_header, "tracks": tracks()}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(bv_raw_to_midi.BvMidiError, "shorter than the latest BV note"):
                bv_raw_to_midi.convert_bv_raw_to_midi(bv_path, allow_standalone=True)

    def test_rejects_header_event_after_duration(self) -> None:
        cases = [
            ("tempoMap", [{"time": 0, "bpm": 120}, {"time": 8, "bpm": 96}]),
            (
                "timeSignatureMap",
                [
                    {"time": 0, "numerator": 4, "denominator": 4},
                    {"time": 8, "numerator": 3, "denominator": 4},
                ],
            ),
        ]

        for field, values in cases:
            with self.subTest(field=field), TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                bv_path = root / "bv_raw.json"
                broken_header = header()
                broken_header[field] = values
                bv_path.write_text(
                    json.dumps({"header": broken_header, "tracks": tracks()}),
                    encoding="utf-8",
                )

                with self.assertRaisesRegex(
                    bv_raw_to_midi.BvMidiError,
                    "shorter than the latest tempo or time signature event",
                ):
                    bv_raw_to_midi.convert_bv_raw_to_midi(
                        bv_path, allow_standalone=True
                    )

    def test_rejects_arrangements_over_piece_limits(self) -> None:
        cases = [
            (
                {
                    **header(),
                    "duration": 4 * 251,
                    "tempoMap": [{"time": 0, "bpm": 240}],
                },
                "250 bars",
            ),
            (
                {
                    **header(),
                    "duration": 4 * 249,
                    "tempoMap": [{"time": 0, "bpm": 96}],
                },
                "10 minutes",
            ),
        ]

        for broken_header, message in cases:
            with self.subTest(message=message), TemporaryDirectory() as temp_dir:
                bv_path = Path(temp_dir) / "bv_raw.json"
                bv_path.write_text(
                    json.dumps({"header": broken_header, "tracks": tracks()}),
                    encoding="utf-8",
                )

                with self.assertRaisesRegex(bv_raw_to_midi.BvMidiError, message):
                    bv_raw_to_midi.convert_bv_raw_to_midi(bv_path)

    def test_rejects_midi_delta_that_exceeds_vlq_limit(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bv_path = root / "bv_raw.json"
            huge_header = header()
            huge_header["duration"] = (bv_raw_to_midi.MAX_MIDI_VLQ + 1) / 480
            empty_tracks = {name: [] for name in EXPECTED_TRACKS}
            bv_path.write_text(
                json.dumps({"header": huge_header, "tracks": empty_tracks}),
                encoding="utf-8",
            )

            with (
                patch.object(bv_raw_to_midi, "MAX_BARS", 10**9),
                patch.object(bv_raw_to_midi, "MAX_SECONDS", 10**9),
                self.assertRaisesRegex(bv_raw_to_midi.BvMidiError, "tick position exceeds"),
            ):
                bv_raw_to_midi.convert_bv_raw_to_midi(
                    bv_path, allow_standalone=True
                )

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

    def test_rejects_cross_pitch_overlap_within_one_track(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            broken_tracks = tracks()
            broken_tracks["VocalPad1"].append(
                {"name": "E4", "midi": 64, "time": 1, "duration": 1}
            )
            bv_path = root / "bv_raw.json"
            bv_path.write_text(
                json.dumps({"header": header(), "tracks": broken_tracks}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                bv_raw_to_midi.BvMidiError,
                "Track VocalPad1 has overlapping notes",
            ):
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
                    allow_standalone=True,
                )
            self.assertEqual(output_path.read_bytes(), b"existing")

    def test_atomic_write_failure_preserves_existing_output(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bv_path = root / "bv_raw.json"
            output_path = root / "custom.mid"
            bv_path.write_text(
                json.dumps({"header": header(), "tracks": tracks()}),
                encoding="utf-8",
            )
            output_path.write_bytes(b"existing")

            with (
                patch.object(Path, "replace", side_effect=OSError("replace failed")),
                self.assertRaisesRegex(OSError, "replace failed"),
            ):
                bv_raw_to_midi.convert_bv_raw_to_midi(
                    bv_path, output_path=output_path, allow_standalone=True
                )

            self.assertEqual(output_path.read_bytes(), b"existing")
            self.assertEqual(list(root.glob(".*.tmp")), [])

    def test_main_handles_input_inspect_os_error(self) -> None:
        stderr = io.StringIO()

        with (
            patch.object(Path, "exists", side_effect=OSError("stat failed")),
            redirect_stderr(stderr),
        ):
            result = bv_raw_to_midi.main(["broken/bv_raw.json"])

        self.assertEqual(result, 2)
        self.assertIn("Unable to inspect bv_raw.json", stderr.getvalue())
        self.assertIn("stat failed", stderr.getvalue())

    def test_main_catches_os_errors_from_conversion(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bv_path = root / "bv_raw.json"
            bv_path.write_text(
                json.dumps({"header": header(), "tracks": tracks()}),
                encoding="utf-8",
            )
            stderr = io.StringIO()

            with (
                patch.object(bv_raw_to_midi, "convert_bv_raw_to_midi", side_effect=OSError("disk full")),
                redirect_stderr(stderr),
            ):
                result = bv_raw_to_midi.main([str(bv_path), "--allow-standalone"])

            self.assertEqual(result, 1)
            self.assertIn("ERROR: disk full", stderr.getvalue())

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
                result = bv_raw_to_midi.main([str(bv_path), "--allow-standalone"])

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
                result = bv_raw_to_midi.main([str(bv_path), "--allow-standalone"])

            self.assertEqual(result, 0)
            self.assertNotEqual(output_path.read_bytes(), b"existing")
            self.assertIn("Wrote MIDI", stdout.getvalue())
            self.assertIn("WARNING: Exported standalone MIDI", stderr.getvalue())


class BvLyricsTest(unittest.TestCase):
    def test_exports_source_lyrics_to_non_pad_tracks(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_melody = melody()
            source_melody[0]["lyric"] = "Hello"
            (root / "melody.json").write_text(
                json.dumps({"header": header(), "melody": source_melody}),
                encoding="utf-8",
            )
            payload_tracks = melody_aligned_tracks()
            for track_name in ("LeadDouble", "HHA", "HHB", "HHF", "LHA", "LHB", "LHF"):
                payload_tracks[track_name][0]["lyric"] = "Hello"
            bv_path = root / "bv_raw.json"
            bv_path.write_text(
                json.dumps({"header": header(), "tracks": payload_tracks}),
                encoding="utf-8",
            )

            output_path = bv_raw_to_midi.convert_bv_raw_to_midi(bv_path)
            _, parsed_tracks, _, _ = midi_to_json.parse_midi(output_path)

        lyrics_by_track = {track.name: [item.text for item in track.lyrics] for track in parsed_tracks}
        for track_name in ("LeadDouble", "HHA", "HHB", "HHF", "LHA", "LHB", "LHF"):
            self.assertEqual(lyrics_by_track[track_name], ["Hello"])
        for track_name in ("VocalPad1", "VocalPad2", "VocalPad3", "VocalPad4"):
            self.assertEqual(lyrics_by_track[track_name], [])

    def test_rejects_wrong_inherited_lyric_and_pad_lyric(self) -> None:
        for track_name, message in (("HHA", "must inherit lyric"), ("VocalPad1", "must not contain lyric")):
            with self.subTest(track=track_name), TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                source_melody = melody()
                source_melody[0]["lyric"] = "Hello"
                (root / "melody.json").write_text(
                    json.dumps({"header": header(), "melody": source_melody}),
                    encoding="utf-8",
                )
                payload_tracks = melody_aligned_tracks()
                for name in ("LeadDouble", "HHA", "HHB", "HHF", "LHA", "LHB", "LHF"):
                    payload_tracks[name][0]["lyric"] = "Hello"
                payload_tracks[track_name][0]["lyric"] = "Wrong"
                bv_path = root / "bv_raw.json"
                bv_path.write_text(
                    json.dumps({"header": header(), "tracks": payload_tracks}),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(bv_raw_to_midi.BvMidiError, message):
                    bv_raw_to_midi.convert_bv_raw_to_midi(bv_path)

    def test_requires_source_by_default_and_allows_lyric_free_standalone(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bv_path = root / "bv_raw.json"
            bv_path.write_text(
                json.dumps({"header": header(), "tracks": tracks()}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(bv_raw_to_midi.BvMidiError, "Missing required source"):
                bv_raw_to_midi.convert_bv_raw_to_midi(bv_path)
            output = bv_raw_to_midi.convert_bv_raw_to_midi(
                bv_path, allow_standalone=True
            )
            self.assertTrue(output.exists())

    def test_rejects_lyrics_in_standalone_export(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload_tracks = tracks()
            payload_tracks["LeadDouble"][0]["lyric"] = "你好"
            bv_path = root / "bv_raw.json"
            bv_path.write_text(
                json.dumps({"header": header(), "tracks": payload_tracks}, ensure_ascii=False),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(bv_raw_to_midi.BvMidiError, "does not allow lyric"):
                bv_raw_to_midi.convert_bv_raw_to_midi(
                    bv_path, allow_standalone=True
                )

    def test_round_trips_utf8_chinese_lyrics(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_melody = melody()
            source_melody[0]["lyric"] = "见面是最棒的事"
            (root / "melody.json").write_text(
                json.dumps({"header": header(), "melody": source_melody}, ensure_ascii=False),
                encoding="utf-8",
            )
            payload_tracks = melody_aligned_tracks()
            for name in ("LeadDouble", "HHA", "HHB", "HHF", "LHA", "LHB", "LHF"):
                payload_tracks[name][0]["lyric"] = "见面是最棒的事"
            bv_path = root / "bv_raw.json"
            bv_path.write_text(
                json.dumps({"header": header(), "tracks": payload_tracks}, ensure_ascii=False),
                encoding="utf-8",
            )

            output = bv_raw_to_midi.convert_bv_raw_to_midi(bv_path)
            _, parsed_tracks, _, _ = midi_to_json.parse_midi(output)

        lyrics = {track.name: [item.text for item in track.lyrics] for track in parsed_tracks}
        self.assertEqual(lyrics["LeadDouble"], ["见面是最棒的事"])


if __name__ == "__main__":
    unittest.main()
