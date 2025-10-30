from __future__ import annotations

import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "script" / "midi_to_json.py"
TICKS_PER_BEAT = 480


def load_midi_to_json_module() -> Any:
    spec = importlib.util.spec_from_file_location("midi_to_json", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


midi_to_json = load_midi_to_json_module()


def vlq(value: int) -> bytes:
    parts = [value & 0x7F]
    value >>= 7
    while value:
        parts.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(parts))


def meta_event(delta: int, meta_type: int, payload: bytes) -> bytes:
    return vlq(delta) + bytes([0xFF, meta_type]) + vlq(len(payload)) + payload


def tempo_payload(bpm: int) -> bytes:
    microseconds_per_quarter = round(60_000_000 / bpm)
    return microseconds_per_quarter.to_bytes(3, "big")


def time_signature_payload(numerator: int, denominator_power: int) -> bytes:
    return bytes([numerator, denominator_power, 24, 8])


def track_chunk(
    name: str | None,
    notes: list[tuple[int, int, int]],
    metas: list[tuple[int, int, bytes]] | None = None,
) -> bytes:
    absolute_events: list[tuple[int, int, bytes]] = []
    if name is not None:
        absolute_events.append((0, 0, bytes([0xFF, 0x03]) + vlq(len(name)) + name.encode("utf-8")))
    for event_time, meta_type, payload in metas or []:
        absolute_events.append((event_time, 1, bytes([0xFF, meta_type]) + vlq(len(payload)) + payload))
    for start, end, midi in notes:
        absolute_events.append((start, 3, bytes([0x90, midi, 64])))
        absolute_events.append((end, 2, bytes([0x80, midi, 64])))

    payload = bytearray()
    previous_time = 0
    for event_time, _, event_payload in sorted(absolute_events, key=lambda item: (item[0], item[1])):
        payload.extend(vlq(event_time - previous_time))
        payload.extend(event_payload)
        previous_time = event_time
    payload.extend(meta_event(0, 0x2F, b""))
    return b"MTrk" + len(payload).to_bytes(4, "big") + bytes(payload)


def midi_file(tracks: list[bytes]) -> bytes:
    header_payload = (
        (1).to_bytes(2, "big")
        + len(tracks).to_bytes(2, "big")
        + TICKS_PER_BEAT.to_bytes(2, "big")
    )
    return b"MThd" + len(header_payload).to_bytes(4, "big") + header_payload + b"".join(tracks)


class MidiToJsonTest(unittest.TestCase):
    def convert(self, midi_bytes: bytes) -> tuple[dict[str, Any], Path, str]:
        self.temp_dir = TemporaryDirectory()
        root = Path(self.temp_dir.name)
        input_path = root / "song.mid"
        input_path.write_bytes(midi_bytes)
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            output_dir = midi_to_json.convert_midi_to_json(input_path, root / "output", 0.01)
        canonical = json.loads((output_dir / "canonical.json").read_text(encoding="utf-8"))
        return canonical, output_dir, stderr.getvalue()

    def tearDown(self) -> None:
        temp_dir = getattr(self, "temp_dir", None)
        if temp_dir is not None:
            temp_dir.cleanup()

    def test_converts_valid_tracks_to_canonical_and_split_json(self) -> None:
        midi_bytes = midi_file(
            [
                track_chunk(
                    " Chord ",
                    [
                        (0, TICKS_PER_BEAT, 48),
                        (0, TICKS_PER_BEAT, 60),
                        (0, TICKS_PER_BEAT, 72),
                        (TICKS_PER_BEAT, TICKS_PER_BEAT * 2, 48),
                        (TICKS_PER_BEAT, TICKS_PER_BEAT * 2, 52),
                        (TICKS_PER_BEAT, TICKS_PER_BEAT * 2, 55),
                        (0, TICKS_PER_BEAT, 11),
                    ],
                    [(0, 0x51, tempo_payload(100))],
                ),
                track_chunk(
                    "MELODY",
                    [
                        (0, TICKS_PER_BEAT, 60),
                        (TICKS_PER_BEAT, TICKS_PER_BEAT * 2, 62),
                    ],
                    [(0, 0x58, time_signature_payload(4, 2))],
                ),
            ]
        )

        canonical, output_dir, stderr = self.convert(midi_bytes)

        self.assertEqual(stderr, "Filtered out-of-range notes: melody=0, chord=1\n")
        self.assertEqual(
            canonical["header"],
            {
                "startTime": 0,
                "duration": 2,
                "timeUnit": "beats",
                "grid": "1/16_bar",
                "tempoMap": [{"time": 0, "bpm": 100}],
                "timeSignatureMap": [{"time": 0, "numerator": 4, "denominator": 4}],
            },
        )
        self.assertEqual(
            canonical["melody"],
            [
                {"name": "C4", "midi": 60, "time": 0, "duration": 1, "bar": 1, "beat": 1, "grid": 0},
                {"name": "D4", "midi": 62, "time": 1, "duration": 1, "bar": 1, "beat": 2, "grid": 4},
            ],
        )
        self.assertEqual(
            canonical["chord"],
            [
                {"name": "C3", "midi": 48, "time": 1, "duration": 1, "bar": 1, "beat": 2, "grid": 4},
                {"name": "E3", "midi": 52, "time": 1, "duration": 1, "bar": 1, "beat": 2, "grid": 4},
                {"name": "G3", "midi": 55, "time": 1, "duration": 1, "bar": 1, "beat": 2, "grid": 4},
            ],
        )
        self.assertEqual(
            json.loads((output_dir / "melody.json").read_text(encoding="utf-8"))["melody"],
            canonical["melody"],
        )
        self.assertEqual(
            json.loads((output_dir / "chord.json").read_text(encoding="utf-8"))["chord"],
            canonical["chord"],
        )

    def test_rejects_any_track_set_other_than_exactly_melody_and_chord(self) -> None:
        cases = [
            ("extra track", ["melody", "chord", "extra"]),
            ("wrong track name", ["melody", "bass"]),
            ("unnamed track", ["melody", None]),
            ("duplicate melody", ["melody", "melody"]),
        ]

        for label, track_names in cases:
            with self.subTest(label=label):
                midi_bytes = midi_file([track_chunk(name, []) for name in track_names])
                with TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    input_path = root / "song.mid"
                    input_path.write_bytes(midi_bytes)
                    with self.assertRaisesRegex(
                        midi_to_json.MidiValidationError,
                        "exactly two tracks named 'melody' and 'chord'",
                    ):
                        midi_to_json.convert_midi_to_json(input_path, root / "output", 0.01)

    def test_rejects_unquantized_notes(self) -> None:
        midi_bytes = midi_file(
            [
                track_chunk("melody", [(10, TICKS_PER_BEAT, 60)]),
                track_chunk("chord", [(0, TICKS_PER_BEAT, 48)]),
            ]
        )

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "song.mid"
            input_path.write_bytes(midi_bytes)
            with self.assertRaisesRegex(midi_to_json.MidiValidationError, "unquantized start"):
                midi_to_json.convert_midi_to_json(input_path, root / "output", 0.01)

    def test_rejects_chord_notes_that_do_not_end_together(self) -> None:
        midi_bytes = midi_file(
            [
                track_chunk("melody", [(0, TICKS_PER_BEAT, 60)]),
                track_chunk(
                    "chord",
                    [
                        (0, TICKS_PER_BEAT, 48),
                        (0, TICKS_PER_BEAT * 2, 52),
                    ],
                ),
            ]
        )

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "song.mid"
            input_path.write_bytes(midi_bytes)
            with self.assertRaisesRegex(midi_to_json.MidiValidationError, "must end together"):
                midi_to_json.convert_midi_to_json(input_path, root / "output", 0.01)

    def test_rejects_overlapping_melody_notes(self) -> None:
        midi_bytes = midi_file(
            [
                track_chunk(
                    "melody",
                    [
                        (0, TICKS_PER_BEAT * 2, 60),
                        (TICKS_PER_BEAT, TICKS_PER_BEAT * 3, 62),
                    ],
                ),
                track_chunk(
                    "chord",
                    [
                        (0, TICKS_PER_BEAT * 2, 48),
                        (0, TICKS_PER_BEAT * 2, 52),
                    ],
                ),
            ]
        )

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "song.mid"
            input_path.write_bytes(midi_bytes)
            with self.assertRaisesRegex(midi_to_json.MidiValidationError, "overlapping notes"):
                midi_to_json.convert_midi_to_json(input_path, root / "output", 0.01)

    def test_filters_chord_groups_that_are_not_real_chords(self) -> None:
        midi_bytes = midi_file(
            [
                track_chunk("melody", [(0, TICKS_PER_BEAT * 3, 60)]),
                track_chunk(
                    "chord",
                    [
                        (0, TICKS_PER_BEAT, 48),
                        (TICKS_PER_BEAT, TICKS_PER_BEAT * 2, 48),
                        (TICKS_PER_BEAT, TICKS_PER_BEAT * 2, 60),
                        (TICKS_PER_BEAT, TICKS_PER_BEAT * 2, 72),
                        (TICKS_PER_BEAT * 2, TICKS_PER_BEAT * 3, 48),
                        (TICKS_PER_BEAT * 2, TICKS_PER_BEAT * 3, 52),
                    ],
                ),
            ]
        )

        canonical, output_dir, _ = self.convert(midi_bytes)

        self.assertEqual(
            canonical["chord"],
            [
                {"name": "C3", "midi": 48, "time": 2, "duration": 1, "bar": 1, "beat": 3, "grid": 8},
                {"name": "E3", "midi": 52, "time": 2, "duration": 1, "bar": 1, "beat": 3, "grid": 8},
            ],
        )
        self.assertEqual(
            json.loads((output_dir / "chord.json").read_text(encoding="utf-8"))["chord"],
            canonical["chord"],
        )


if __name__ == "__main__":
    unittest.main()
