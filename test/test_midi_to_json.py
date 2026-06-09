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


def tempo_payload(bpm: float) -> bytes:
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


def midi_file(tracks: list[bytes], midi_format: int = 1, ticks_per_beat: int = TICKS_PER_BEAT) -> bytes:
    header_payload = (
        midi_format.to_bytes(2, "big")
        + len(tracks).to_bytes(2, "big")
        + ticks_per_beat.to_bytes(2, "big")
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
                "grid": "1/32_bar",
                "tempoMap": [{"time": 0, "bpm": 100}],
                "timeSignatureMap": [{"time": 0, "numerator": 4, "denominator": 4}],
            },
        )
        self.assertEqual(
            canonical["melody"],
            [
                {"name": "C4", "midi": 60, "time": 0, "duration": 1, "bar": 1, "beat": 1, "grid": 0},
                {"name": "D4", "midi": 62, "time": 1, "duration": 1, "bar": 1, "beat": 2, "grid": 8},
            ],
        )
        self.assertEqual(
            canonical["chord"],
            [
                {"name": "C3", "midi": 48, "time": 1, "duration": 1, "bar": 1, "beat": 2, "grid": 8},
                {"name": "E3", "midi": 52, "time": 1, "duration": 1, "bar": 1, "beat": 2, "grid": 8},
                {"name": "G3", "midi": 55, "time": 1, "duration": 1, "bar": 1, "beat": 2, "grid": 8},
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

    def test_preserves_quarter_bpm_tempo_events(self) -> None:
        midi_bytes = midi_file(
            [
                track_chunk(
                    "melody",
                    [(0, TICKS_PER_BEAT, 60)],
                    [(0, 0x51, tempo_payload(130.25))],
                ),
                track_chunk("chord", [(0, TICKS_PER_BEAT, 48), (0, TICKS_PER_BEAT, 52)]),
            ]
        )

        canonical, _, _ = self.convert(midi_bytes)

        self.assertEqual(canonical["header"]["tempoMap"], [{"time": 0, "bpm": 130.25}])

    def test_normalizes_high_melody_down_by_a_whole_octave(self) -> None:
        midi_bytes = midi_file(
            [
                track_chunk(
                    "melody",
                    [
                        (0, TICKS_PER_BEAT, 82),
                        (TICKS_PER_BEAT, TICKS_PER_BEAT * 2, 84),
                    ],
                ),
                track_chunk(
                    "chord",
                    [
                        (0, TICKS_PER_BEAT * 2, 60),
                        (0, TICKS_PER_BEAT * 2, 64),
                    ],
                ),
            ]
        )

        canonical, _, stderr = self.convert(midi_bytes)

        self.assertEqual([note["midi"] for note in canonical["melody"]], [70, 72])
        self.assertEqual([note["name"] for note in canonical["melody"]], ["A#4", "C5"])
        self.assertEqual(
            stderr,
            "Normalized melody register for harmony headroom: -12 semitones.\n",
        )

    def test_normalizes_low_melody_up_by_a_whole_octave(self) -> None:
        midi_bytes = midi_file(
            [
                track_chunk(
                    "melody",
                    [
                        (0, TICKS_PER_BEAT, 12),
                        (TICKS_PER_BEAT, TICKS_PER_BEAT * 2, 14),
                    ],
                ),
                track_chunk(
                    "chord",
                    [
                        (0, TICKS_PER_BEAT * 2, 36),
                        (0, TICKS_PER_BEAT * 2, 40),
                    ],
                ),
            ]
        )

        canonical, _, stderr = self.convert(midi_bytes)

        self.assertEqual([note["midi"] for note in canonical["melody"]], [24, 26])
        self.assertEqual([note["name"] for note in canonical["melody"]], ["C1", "D1"])
        self.assertEqual(
            stderr,
            "Normalized melody register for harmony headroom: +12 semitones.\n",
        )

    def test_rejects_melody_too_wide_for_uniform_octave_normalization(self) -> None:
        midi_bytes = midi_file(
            [
                track_chunk(
                    "melody",
                    [
                        (0, TICKS_PER_BEAT, 12),
                        (TICKS_PER_BEAT, TICKS_PER_BEAT * 2, 84),
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
            with self.assertRaisesRegex(
                midi_to_json.MidiValidationError,
                "no uniform whole-octave shift",
            ):
                midi_to_json.convert_midi_to_json(input_path, root / "output", 0.01)

    def test_rejects_tempo_events_finer_than_quarter_bpm(self) -> None:
        midi_bytes = midi_file(
            [
                track_chunk(
                    "melody",
                    [(0, TICKS_PER_BEAT, 60)],
                    [(0, 0x51, tempo_payload(130.125))],
                ),
                track_chunk("chord", [(0, TICKS_PER_BEAT, 48), (0, TICKS_PER_BEAT, 52)]),
            ]
        )

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "song.mid"
            input_path.write_bytes(midi_bytes)
            with self.assertRaisesRegex(midi_to_json.MidiParseError, "0.25 BPM increments"):
                midi_to_json.convert_midi_to_json(input_path, root / "output", 0.01)

    def test_rejects_tolerance_values_that_can_bypass_validation(self) -> None:
        with self.assertRaisesRegex(midi_to_json.MidiValidationError, "tolerance must be between"):
            midi_to_json.validate_tolerance(midi_to_json.MAX_TOLERANCE + 0.001)

    def test_no_overwrite_json_write_rolls_back_on_race(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "first.json"
            second = root / "second.json"
            second.write_text("existing", encoding="utf-8")

            with self.assertRaisesRegex(midi_to_json.MidiValidationError, "already exists"):
                midi_to_json.write_json_files(
                    [(first, {"value": 1}), (second, {"value": 2})],
                    overwrite=False,
                )

            self.assertFalse(first.exists())
            self.assertEqual(second.read_text(encoding="utf-8"), "existing")

    def test_rejects_missing_or_duplicate_required_tracks(self) -> None:
        cases = [
            (
                "wrong track name",
                [
                    ("melody", [(0, TICKS_PER_BEAT, 60)]),
                    ("bass", [(0, TICKS_PER_BEAT, 48)]),
                ],
            ),
            (
                "unnamed track",
                [
                    ("melody", [(0, TICKS_PER_BEAT, 60)]),
                    (None, [(0, TICKS_PER_BEAT, 48)]),
                ],
            ),
            (
                "duplicate melody",
                [
                    ("melody", [(0, TICKS_PER_BEAT, 60)]),
                    ("melody", [(TICKS_PER_BEAT, TICKS_PER_BEAT * 2, 62)]),
                ],
            ),
        ]

        for label, tracks in cases:
            with self.subTest(label=label):
                midi_bytes = midi_file([track_chunk(name, notes) for name, notes in tracks])
                with TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    input_path = root / "song.mid"
                    input_path.write_bytes(midi_bytes)
                    with self.assertRaisesRegex(
                        midi_to_json.MidiValidationError,
                        "exactly one non-empty track named 'melody'",
                    ):
                        midi_to_json.convert_midi_to_json(input_path, root / "output", 0.01)

    def test_accepts_control_track_and_ignores_other_non_required_tracks(self) -> None:
        midi_bytes = midi_file(
            [
                track_chunk("melody", [(0, TICKS_PER_BEAT * 4, 60)]),
                track_chunk(
                    "chord",
                    [
                        (0, TICKS_PER_BEAT * 4, 48),
                        (0, TICKS_PER_BEAT * 4, 52),
                    ],
                ),
                track_chunk(
                    "control",
                    [
                        (0, TICKS_PER_BEAT, 0),
                        (TICKS_PER_BEAT * 2, TICKS_PER_BEAT * 3, 2),
                        (TICKS_PER_BEAT * 3, TICKS_PER_BEAT * 4, 4),
                    ],
                ),
                track_chunk("ignored arrangement", [(0, TICKS_PER_BEAT, 72)]),
            ]
        )

        canonical, output_dir, stderr = self.convert(midi_bytes)

        self.assertEqual(stderr, "")
        self.assertEqual(
            canonical["control"],
            [
                {
                    "section": "verse",
                    "name": "C-1",
                    "midi": 0,
                    "time": 0,
                    "duration": 2,
                    "bar": 1,
                    "beat": 1,
                    "grid": 0,
                },
                {
                    "section": "chorus",
                    "name": "D-1",
                    "midi": 2,
                    "time": 2,
                    "duration": 1,
                    "bar": 1,
                    "beat": 3,
                    "grid": 16,
                },
                {
                    "section": "no_bv",
                    "name": "E-1",
                    "midi": 4,
                    "time": 3,
                    "duration": 1,
                    "bar": 1,
                    "beat": 4,
                    "grid": 24,
                },
            ],
        )
        self.assertEqual(
            json.loads((output_dir / "melody.json").read_text(encoding="utf-8"))["control"],
            canonical["control"],
        )

    def test_accepts_control_notes_in_zero_octave(self) -> None:
        midi_bytes = midi_file(
            [
                track_chunk("melody", [(0, TICKS_PER_BEAT * 4, 60)]),
                track_chunk(
                    "chord",
                    [
                        (0, TICKS_PER_BEAT * 4, 48),
                        (0, TICKS_PER_BEAT * 4, 52),
                    ],
                ),
                track_chunk(
                    "control",
                    [
                        (0, TICKS_PER_BEAT, 12),
                        (TICKS_PER_BEAT * 2, TICKS_PER_BEAT * 3, 14),
                        (TICKS_PER_BEAT * 3, TICKS_PER_BEAT * 4, 16),
                    ],
                ),
            ]
        )

        canonical, _, _ = self.convert(midi_bytes)

        self.assertEqual(
            canonical["control"],
            [
                {
                    "section": "verse",
                    "name": "C0",
                    "midi": 12,
                    "time": 0,
                    "duration": 2,
                    "bar": 1,
                    "beat": 1,
                    "grid": 0,
                },
                {
                    "section": "chorus",
                    "name": "D0",
                    "midi": 14,
                    "time": 2,
                    "duration": 1,
                    "bar": 1,
                    "beat": 3,
                    "grid": 16,
                },
                {
                    "section": "no_bv",
                    "name": "E0",
                    "midi": 16,
                    "time": 3,
                    "duration": 1,
                    "bar": 1,
                    "beat": 4,
                    "grid": 24,
                },
            ],
        )

    def test_rejects_overlapping_control_note_coverage(self) -> None:
        midi_bytes = midi_file(
            [
                track_chunk("melody", [(0, TICKS_PER_BEAT * 4, 60)]),
                track_chunk(
                    "chord",
                    [
                        (0, TICKS_PER_BEAT * 4, 48),
                        (0, TICKS_PER_BEAT * 4, 52),
                    ],
                ),
                track_chunk(
                    "control",
                    [
                        (0, TICKS_PER_BEAT * 3, 0),
                        (TICKS_PER_BEAT * 2, TICKS_PER_BEAT * 4, 14),
                    ],
                ),
            ]
        )

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "song.mid"
            input_path.write_bytes(midi_bytes)
            with self.assertRaisesRegex(midi_to_json.MidiValidationError, "must not overlap"):
                midi_to_json.convert_midi_to_json(input_path, root / "output", 0.01)

    def test_rejects_duplicate_control_tracks(self) -> None:
        midi_bytes = midi_file(
            [
                track_chunk("melody", [(0, TICKS_PER_BEAT, 60)]),
                track_chunk("chord", [(0, TICKS_PER_BEAT, 48), (0, TICKS_PER_BEAT, 52)]),
                track_chunk("control", [(0, TICKS_PER_BEAT, 0)]),
                track_chunk("control", [(TICKS_PER_BEAT, TICKS_PER_BEAT * 2, 2)]),
            ]
        )

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "song.mid"
            input_path.write_bytes(midi_bytes)
            with self.assertRaisesRegex(midi_to_json.MidiValidationError, "more than one"):
                midi_to_json.convert_midi_to_json(input_path, root / "output", 0.01)

    def test_ignores_empty_tracks_including_duplicate_names(self) -> None:
        midi_bytes = midi_file(
            [
                track_chunk("melody", [], [(0, 0x51, tempo_payload(100))]),
                track_chunk("chord", [], [(0, 0x58, time_signature_payload(4, 2))]),
                track_chunk("melody", [(0, TICKS_PER_BEAT, 60)]),
                track_chunk(
                    "chord",
                    [
                        (0, TICKS_PER_BEAT, 48),
                        (0, TICKS_PER_BEAT, 52),
                    ],
                ),
                track_chunk("unused", []),
                track_chunk(None, []),
            ]
        )

        canonical, _, stderr = self.convert(midi_bytes)

        self.assertEqual(stderr, "")
        self.assertEqual(canonical["header"]["tempoMap"], [{"time": 0, "bpm": 100}])
        self.assertEqual(
            canonical["header"]["timeSignatureMap"],
            [{"time": 0, "numerator": 4, "denominator": 4}],
        )
        self.assertEqual(
            canonical["melody"],
            [
                {"name": "C4", "midi": 60, "time": 0, "duration": 1, "bar": 1, "beat": 1, "grid": 0},
            ],
        )
        self.assertEqual(
            canonical["chord"],
            [
                {"name": "C3", "midi": 48, "time": 0, "duration": 1, "bar": 1, "beat": 1, "grid": 0},
                {"name": "E3", "midi": 52, "time": 0, "duration": 1, "bar": 1, "beat": 1, "grid": 0},
            ],
        )

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
                {"name": "C3", "midi": 48, "time": 2, "duration": 1, "bar": 1, "beat": 3, "grid": 16},
                {"name": "E3", "midi": 52, "time": 2, "duration": 1, "bar": 1, "beat": 3, "grid": 16},
            ],
        )
        self.assertEqual(
            json.loads((output_dir / "chord.json").read_text(encoding="utf-8"))["chord"],
            canonical["chord"],
        )

    def test_rejects_when_melody_has_no_valid_notes_after_filtering(self) -> None:
        midi_bytes = midi_file(
            [
                track_chunk("melody", [(0, TICKS_PER_BEAT, 11)]),
                track_chunk(
                    "chord",
                    [
                        (0, TICKS_PER_BEAT, 48),
                        (0, TICKS_PER_BEAT, 52),
                    ],
                ),
            ]
        )

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "song.mid"
            input_path.write_bytes(midi_bytes)
            with self.assertRaisesRegex(midi_to_json.MidiValidationError, "Melody track"):
                midi_to_json.convert_midi_to_json(input_path, root / "output", 0.01)

    def test_rejects_when_chord_has_no_valid_chords_after_filtering(self) -> None:
        midi_bytes = midi_file(
            [
                track_chunk("melody", [(0, TICKS_PER_BEAT, 60)]),
                track_chunk(
                    "chord",
                    [
                        (0, TICKS_PER_BEAT, 48),
                        (0, TICKS_PER_BEAT, 60),
                        (0, TICKS_PER_BEAT, 72),
                    ],
                ),
            ]
        )

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "song.mid"
            input_path.write_bytes(midi_bytes)
            with self.assertRaisesRegex(midi_to_json.MidiValidationError, "at least one valid chord"):
                midi_to_json.convert_midi_to_json(input_path, root / "output", 0.01)

    def test_rejects_when_no_valid_chord_overlaps_melody(self) -> None:
        midi_bytes = midi_file(
            [
                track_chunk("melody", [(0, TICKS_PER_BEAT, 60)]),
                track_chunk(
                    "chord",
                    [
                        (TICKS_PER_BEAT * 2, TICKS_PER_BEAT * 3, 48),
                        (TICKS_PER_BEAT * 2, TICKS_PER_BEAT * 3, 52),
                    ],
                ),
            ]
        )

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "song.mid"
            input_path.write_bytes(midi_bytes)
            with self.assertRaisesRegex(midi_to_json.MidiValidationError, "overlaps a melody note"):
                midi_to_json.convert_midi_to_json(input_path, root / "output", 0.01)

    def test_rejects_when_midi_exceeds_250_bars(self) -> None:
        midi_bytes = midi_file(
            [
                track_chunk(
                    "melody",
                    [(0, TICKS_PER_BEAT * 4 * 251, 60)],
                    [(0, 0x51, tempo_payload(240))],
                ),
                track_chunk(
                    "chord",
                    [
                        (0, TICKS_PER_BEAT * 4 * 251, 48),
                        (0, TICKS_PER_BEAT * 4 * 251, 52),
                    ],
                ),
            ]
        )

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "song.mid"
            input_path.write_bytes(midi_bytes)
            with self.assertRaisesRegex(midi_to_json.MidiValidationError, "250 bars"):
                midi_to_json.convert_midi_to_json(input_path, root / "output", 0.01)

    def test_rejects_when_midi_exceeds_10_minutes(self) -> None:
        midi_bytes = midi_file(
            [
                track_chunk(
                    "melody",
                    [(0, TICKS_PER_BEAT * 4 * 249, 60)],
                    [(0, 0x51, tempo_payload(96))],
                ),
                track_chunk(
                    "chord",
                    [
                        (0, TICKS_PER_BEAT * 4 * 249, 48),
                        (0, TICKS_PER_BEAT * 4 * 249, 52),
                    ],
                ),
            ]
        )

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "song.mid"
            input_path.write_bytes(midi_bytes)
            with self.assertRaisesRegex(midi_to_json.MidiValidationError, "10 minutes"):
                midi_to_json.convert_midi_to_json(input_path, root / "output", 0.01)

    def test_rejects_header_events_after_final_note_end(self) -> None:
        cases = [
            ("tempo", [(TICKS_PER_BEAT * 2, 0x51, tempo_payload(100))]),
            (
                "time signature",
                [(TICKS_PER_BEAT * 4, 0x58, time_signature_payload(3, 2))],
            ),
        ]

        for label, metas in cases:
            with self.subTest(label=label), TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                input_path = root / "song.mid"
                input_path.write_bytes(
                    midi_file(
                        [
                            track_chunk("melody", [(0, TICKS_PER_BEAT, 60)], metas),
                            track_chunk(
                                "chord",
                                [(0, TICKS_PER_BEAT, 48), (0, TICKS_PER_BEAT, 52)],
                            ),
                        ]
                    )
                )

                with self.assertRaisesRegex(midi_to_json.MidiValidationError, "after the final note"):
                    midi_to_json.convert_midi_to_json(input_path, root / "output", 0.01)

                self.assertFalse((root / "output" / "song").exists())

    def test_rejects_midi_file_larger_than_parser_limit(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "too-large.mid"
            input_path.write_bytes(b"0" * 17)

            with (
                patch.object(midi_to_json, "MAX_MIDI_FILE_BYTES", 16),
                self.assertRaisesRegex(midi_to_json.MidiParseError, "file size exceeds limit"),
            ):
                midi_to_json.parse_midi(input_path)

    def test_rejects_midi_that_grows_after_size_inspection(self) -> None:
        class GrowingMidiPath:
            def stat(self) -> Any:
                return type("Stat", (), {"st_size": 1})()

            def open(self, mode: str) -> io.BytesIO:
                self.mode = mode
                return io.BytesIO(b"0" * (midi_to_json.MAX_MIDI_FILE_BYTES + 1))

        with self.assertRaisesRegex(midi_to_json.MidiParseError, "file size exceeds limit"):
            midi_to_json.parse_midi(GrowingMidiPath())

    def test_rejects_midi_with_too_many_tracks_before_parsing_tracks(self) -> None:
        midi_bytes = midi_file([track_chunk(None, []) for _ in range(3)])

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "too-many-tracks.mid"
            input_path.write_bytes(midi_bytes)

            with (
                patch.object(midi_to_json, "MAX_TRACKS", 2),
                self.assertRaisesRegex(midi_to_json.MidiParseError, "track count exceeds limit"),
            ):
                midi_to_json.parse_midi(input_path)

    def test_rejects_format_zero_with_multiple_tracks(self) -> None:
        midi_bytes = midi_file(
            [track_chunk("melody", []), track_chunk("chord", [])],
            midi_format=0,
        )

        with TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "format-zero.mid"
            input_path.write_bytes(midi_bytes)

            with self.assertRaisesRegex(
                midi_to_json.MidiParseError,
                "format 0 must declare exactly one track",
            ):
                midi_to_json.parse_midi(input_path)

    def test_rejects_midi_without_declared_tracks(self) -> None:
        midi_bytes = midi_file([])

        with TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "no-tracks.mid"
            input_path.write_bytes(midi_bytes)

            with self.assertRaisesRegex(midi_to_json.MidiParseError, "at least one track"):
                midi_to_json.parse_midi(input_path)

    def test_rejects_format_two(self) -> None:
        midi_bytes = midi_file([track_chunk("sequence", [])], midi_format=2)

        with TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "format-two.mid"
            input_path.write_bytes(midi_bytes)

            with self.assertRaisesRegex(midi_to_json.MidiParseError, "Unsupported MIDI format: 2"):
                midi_to_json.parse_midi(input_path)

    def test_rejects_trailing_bytes_after_declared_tracks(self) -> None:
        midi_bytes = midi_file([track_chunk("melody", [])]) + b"garbage"

        with TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "trailing.mid"
            input_path.write_bytes(midi_bytes)

            with self.assertRaisesRegex(midi_to_json.MidiParseError, "7 trailing byte"):
                midi_to_json.parse_midi(input_path)

    def test_rejects_track_without_end_of_track_event(self) -> None:
        payload = meta_event(0, 0x03, b"melody")

        with self.assertRaisesRegex(midi_to_json.MidiParseError, "missing End-of-Track"):
            midi_to_json.parse_track(payload, 0, 0)

    def test_rejects_non_empty_end_of_track_payload(self) -> None:
        payload = meta_event(0, 0x2F, b"x")

        with self.assertRaisesRegex(midi_to_json.MidiParseError, "payload must be empty"):
            midi_to_json.parse_track(payload, 0, 0)

    def test_rejects_data_or_duplicate_event_after_end_of_track(self) -> None:
        cases = [
            meta_event(0, 0x2F, b"") + meta_event(0, 0x03, b"late"),
            meta_event(0, 0x2F, b"") + meta_event(0, 0x2F, b""),
        ]

        for payload in cases:
            with self.subTest(payload=payload), self.assertRaisesRegex(
                midi_to_json.MidiParseError,
                "data found after End-of-Track",
            ):
                midi_to_json.parse_track(payload, 0, 0)

    def test_pairs_nested_same_pitch_notes_in_fifo_order(self) -> None:
        payload = (
            b"\x00\x90\x3c\x40"
            + b"\x0a\x90\x3c\x40"
            + b"\x0a\x80\x3c\x00"
            + b"\x0a\x80\x3c\x00"
            + meta_event(0, 0x2F, b"")
        )

        track, _, _, _ = midi_to_json.parse_track(payload, 0, 0)

        self.assertEqual(
            [(note.start_ticks, note.end_ticks) for note in track.notes],
            [(0, 20), (10, 30)],
        )

    def test_handles_many_nested_same_pitch_notes(self) -> None:
        note_count = 10_000
        payload = (
            b"\x00\x90\x3c\x40" * note_count
            + b"\x00\x80\x3c\x00" * note_count
            + meta_event(0, 0x2F, b"")
        )

        track, _, _, _ = midi_to_json.parse_track(payload, 0, 0)

        self.assertEqual(len(track.notes), note_count)

    def test_rejects_midi_with_too_many_events_during_track_parse(self) -> None:
        midi_bytes = midi_file(
            [
                track_chunk(
                    "melody",
                    [
                        (0, TICKS_PER_BEAT, 60),
                        (TICKS_PER_BEAT, TICKS_PER_BEAT * 2, 62),
                    ],
                )
            ]
        )

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "too-many-events.mid"
            input_path.write_bytes(midi_bytes)

            with (
                patch.object(midi_to_json, "MAX_MIDI_EVENTS", 3),
                self.assertRaisesRegex(midi_to_json.MidiParseError, "event count exceeds limit"),
            ):
                midi_to_json.parse_midi(input_path)

    def test_rejects_midi_with_too_many_parsed_notes(self) -> None:
        midi_bytes = midi_file(
            [
                track_chunk(
                    "melody",
                    [
                        (0, TICKS_PER_BEAT, 60),
                        (TICKS_PER_BEAT, TICKS_PER_BEAT * 2, 62),
                    ],
                )
            ]
        )

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "too-many-notes.mid"
            input_path.write_bytes(midi_bytes)

            with (
                patch.object(midi_to_json, "MAX_PARSED_NOTES", 1),
                self.assertRaisesRegex(midi_to_json.MidiParseError, "parsed note count exceeds limit"),
            ):
                midi_to_json.parse_midi(input_path)

    def test_rejects_global_parsed_note_limit_across_tracks(self) -> None:
        midi_bytes = midi_file(
            [
                track_chunk("one", [(0, TICKS_PER_BEAT, 60)]),
                track_chunk("two", [(0, TICKS_PER_BEAT, 62)]),
            ]
        )

        with TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "too-many-global-notes.mid"
            input_path.write_bytes(midi_bytes)

            with (
                patch.object(midi_to_json, "MAX_PARSED_NOTES", 1),
                self.assertRaisesRegex(midi_to_json.MidiParseError, "parsed note count exceeds limit"),
            ):
                midi_to_json.parse_midi(input_path)

    def test_rejects_global_tempo_event_limit_across_tracks(self) -> None:
        midi_bytes = midi_file(
            [
                track_chunk("one", [], [(0, 0x51, tempo_payload(120))]),
                track_chunk("two", [], [(0, 0x51, tempo_payload(120))]),
            ]
        )

        with TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "too-many-global-tempos.mid"
            input_path.write_bytes(midi_bytes)

            with (
                patch.object(midi_to_json, "MAX_TEMPO_EVENTS", 1),
                self.assertRaisesRegex(midi_to_json.MidiParseError, "tempo event count exceeds limit"),
            ):
                midi_to_json.parse_midi(input_path)

    def test_rejects_global_time_signature_event_limit_across_tracks(self) -> None:
        midi_bytes = midi_file(
            [
                track_chunk("one", [], [(0, 0x58, time_signature_payload(4, 2))]),
                track_chunk("two", [], [(0, 0x58, time_signature_payload(4, 2))]),
            ]
        )

        with TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "too-many-global-time-signatures.mid"
            input_path.write_bytes(midi_bytes)

            with (
                patch.object(midi_to_json, "MAX_TIME_SIGNATURE_EVENTS", 1),
                self.assertRaisesRegex(
                    midi_to_json.MidiParseError,
                    "time signature event count exceeds limit",
                ),
            ):
                midi_to_json.parse_midi(input_path)

    def test_main_rejects_directory_input_with_stable_error(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stderr = io.StringIO()

            with redirect_stderr(stderr):
                result = midi_to_json.main([str(root)])

            self.assertEqual(result, 2)
            self.assertIn("Input MIDI path is not a file", stderr.getvalue())

    def test_main_catches_os_errors_from_conversion(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "song.mid"
            input_path.write_bytes(b"MThd")
            stderr = io.StringIO()

            with (
                patch.object(midi_to_json, "convert_midi_to_json", side_effect=OSError("disk full")),
                redirect_stderr(stderr),
            ):
                result = midi_to_json.main([str(input_path)])

            self.assertEqual(result, 1)
            self.assertIn("ERROR: disk full", stderr.getvalue())

    def test_main_handles_output_inspect_os_error(self) -> None:
        with TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "song.mid"
            input_path.write_bytes(b"MThd")
            stderr = io.StringIO()

            with (
                patch.object(
                    midi_to_json,
                    "existing_output_json_paths",
                    side_effect=OSError("stat failed"),
                ),
                redirect_stderr(stderr),
            ):
                result = midi_to_json.main([str(input_path)])

            self.assertEqual(result, 2)
            self.assertIn("Unable to inspect output JSON files", stderr.getvalue())
            self.assertIn("stat failed", stderr.getvalue())

    def test_json_prepare_failure_preserves_all_existing_outputs(self) -> None:
        midi_bytes = midi_file(
            [
                track_chunk("melody", [(0, TICKS_PER_BEAT, 60)]),
                track_chunk("chord", [(0, TICKS_PER_BEAT, 48), (0, TICKS_PER_BEAT, 52)]),
            ]
        )

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "song.mid"
            output_dir = root / "output" / "song"
            input_path.write_bytes(midi_bytes)
            output_dir.mkdir(parents=True)
            original_contents = {
                output_dir / name: f"existing {name}" for name in midi_to_json.OUTPUT_JSON_FILENAMES
            }
            for path, content in original_contents.items():
                path.write_text(content, encoding="utf-8")

            real_prepare = midi_to_json.prepare_temp_text
            call_count = 0

            def fail_second_prepare(path: Path, text: str) -> Path:
                nonlocal call_count
                call_count += 1
                if call_count == 2:
                    raise OSError("disk full")
                return real_prepare(path, text)

            with (
                patch.object(midi_to_json, "prepare_temp_text", side_effect=fail_second_prepare),
                self.assertRaisesRegex(OSError, "disk full"),
            ):
                midi_to_json.convert_midi_to_json(input_path, root / "output", 0.01)

            for path, content in original_contents.items():
                self.assertEqual(path.read_text(encoding="utf-8"), content)
            self.assertEqual(list(output_dir.glob(".*.tmp")), [])

    def test_main_cancels_when_output_json_exists_and_user_does_not_confirm(self) -> None:
        midi_bytes = midi_file(
            [
                track_chunk("melody", [(0, TICKS_PER_BEAT, 60)]),
                track_chunk("chord", [(0, TICKS_PER_BEAT, 48), (0, TICKS_PER_BEAT, 52)]),
            ]
        )

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "song.mid"
            output_dir = root / "output" / "song"
            canonical_path = output_dir / "canonical.json"
            input_path.write_bytes(midi_bytes)
            output_dir.mkdir(parents=True)
            canonical_path.write_text("existing", encoding="utf-8")

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                patch("builtins.input", return_value="n"),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                result = midi_to_json.main([str(input_path), "--output-root", str(root / "output")])

            self.assertEqual(result, 2)
            self.assertEqual(canonical_path.read_text(encoding="utf-8"), "existing")
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("Canceled", stderr.getvalue())

    def test_main_overwrites_existing_output_json_after_y_confirmation(self) -> None:
        midi_bytes = midi_file(
            [
                track_chunk("melody", [(0, TICKS_PER_BEAT, 60)]),
                track_chunk("chord", [(0, TICKS_PER_BEAT, 48), (0, TICKS_PER_BEAT, 52)]),
            ]
        )

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "song.mid"
            output_dir = root / "output" / "song"
            canonical_path = output_dir / "canonical.json"
            input_path.write_bytes(midi_bytes)
            output_dir.mkdir(parents=True)
            canonical_path.write_text("existing", encoding="utf-8")

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                patch("builtins.input", return_value="y"),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                result = midi_to_json.main([str(input_path), "--output-root", str(root / "output")])

            self.assertEqual(result, 0)
            self.assertNotEqual(canonical_path.read_text(encoding="utf-8"), "existing")
            self.assertIn("Wrote JSON files", stdout.getvalue())
            self.assertEqual(stderr.getvalue(), "")

    def test_accepts_triplet_quantized_melody_notes(self) -> None:
        triplet_eighth_ticks = TICKS_PER_BEAT // 3
        midi_bytes = midi_file(
            [
                track_chunk(
                    "melody",
                    [
                        (0, triplet_eighth_ticks, 60),
                        (triplet_eighth_ticks, triplet_eighth_ticks * 2, 62),
                        (triplet_eighth_ticks * 2, TICKS_PER_BEAT, 64),
                    ],
                ),
                track_chunk(
                    "chord",
                    [
                        (0, TICKS_PER_BEAT, 48),
                        (0, TICKS_PER_BEAT, 52),
                    ],
                ),
            ]
        )

        canonical, _, stderr = self.convert(midi_bytes)

        self.assertEqual(stderr, "")
        self.assertEqual(
            canonical["melody"],
            [
                {
                    "name": "C4",
                    "midi": 60,
                    "time": 0,
                    "duration": 0.333333,
                    "bar": 1,
                    "beat": 1,
                    "grid": 0,
                },
                {
                    "name": "D4",
                    "midi": 62,
                    "time": 0.333333,
                    "duration": 0.333333,
                    "bar": 1,
                    "beat": 1.333333,
                    "grid": 3,
                },
                {
                    "name": "E4",
                    "midi": 64,
                    "time": 0.666667,
                    "duration": 0.333333,
                    "bar": 1,
                    "beat": 1.666667,
                    "grid": 5,
                },
            ],
        )


class RoleMidiAndLyricsTest(unittest.TestCase):
    def test_selects_single_track_role_files_with_different_ticks_per_beat(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            melody_path = root / "melody.mid"
            chord_path = root / "chord.mid"
            melody_path.write_bytes(midi_file([track_chunk("voice", [(0, 480, 60)])]))
            chord_path.write_bytes(
                midi_file(
                    [track_chunk("harmony", [(0, 960, 48), (0, 960, 52)])],
                    ticks_per_beat=960,
                )
            )

            output = midi_to_json.convert_role_midis_to_json(
                melody_path, chord_path, None, root / "output", "song"
            )
            canonical = json.loads((output / "canonical.json").read_text(encoding="utf-8"))

        self.assertEqual(canonical["header"]["duration"], 1)
        self.assertEqual(len(canonical["melody"]), 1)
        self.assertEqual(len(canonical["chord"]), 2)

    def test_ignores_empty_tracks_before_selecting_single_role_track(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            melody_path = root / "melody.mid"
            chord_path = root / "chord.mid"
            melody_path.write_bytes(
                midi_file(
                    [
                        track_chunk("conductor", []),
                        track_chunk("voice", [(0, 480, 60)]),
                        track_chunk("empty", []),
                    ]
                )
            )
            chord_path.write_bytes(
                midi_file(
                    [
                        track_chunk("empty", []),
                        track_chunk("harmony", [(0, 480, 48), (0, 480, 52)]),
                    ]
                )
            )

            output = midi_to_json.convert_role_midis_to_json(
                melody_path, chord_path, None, root / "output"
            )
            canonical = json.loads((output / "canonical.json").read_text(encoding="utf-8"))

        self.assertEqual(len(canonical["melody"]), 1)
        self.assertEqual(len(canonical["chord"]), 2)

    def test_uses_only_melody_file_header(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            melody_path = root / "melody.mid"
            chord_path = root / "chord.mid"
            melody_path.write_bytes(
                midi_file([track_chunk("melody", [(0, 480, 60)], [(0, 0x51, tempo_payload(120))])])
            )
            chord_path.write_bytes(
                midi_file(
                    [track_chunk("chord", [(0, 480, 48), (0, 480, 52)], [(0, 0x51, tempo_payload(122))])]
                )
            )

            output = midi_to_json.convert_role_midis_to_json(
                melody_path, chord_path, None, root / "output"
            )
            canonical = json.loads((output / "canonical.json").read_text(encoding="utf-8"))

        self.assertEqual(canonical["header"]["tempoMap"], [{"time": 0, "bpm": 120}])

    def test_selects_named_roles_from_shared_multi_track_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            melody_path = root / "melody.mid"
            accompaniment_path = root / "accompaniment.mid"
            melody_path.write_bytes(
                midi_file(
                    [
                        track_chunk("conductor", []),
                        track_chunk("melody", [(0, 960, 60)]),
                    ],
                    ticks_per_beat=960,
                )
            )
            accompaniment_path.write_bytes(
                midi_file(
                    [
                        track_chunk("chord", [(0, 480, 48), (0, 480, 52)]),
                        track_chunk("control", [(0, 480, 2)]),
                    ]
                )
            )

            output = midi_to_json.convert_role_midis_to_json(
                melody_path,
                accompaniment_path,
                accompaniment_path,
                root / "output",
            )
            canonical = json.loads((output / "canonical.json").read_text(encoding="utf-8"))

        self.assertEqual(canonical["header"]["duration"], 1)
        self.assertEqual(canonical["control"][0]["section"], "chorus")

    def test_rejects_missing_named_role_in_multi_track_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            melody_path = root / "melody.mid"
            chord_path = root / "chord.mid"
            melody_path.write_bytes(
                midi_file(
                    [
                        track_chunk("voice", [(0, 480, 60)]),
                        track_chunk("other", [(0, 480, 62)]),
                    ]
                )
            )
            chord_path.write_bytes(
                midi_file(
                    [
                        track_chunk("chord", [(0, 480, 48), (0, 480, 52)]),
                    ]
                )
            )

            with self.assertRaisesRegex(midi_to_json.MidiValidationError, "none is named 'melody'"):
                midi_to_json.convert_role_midis_to_json(
                    melody_path, chord_path, None, root / "output"
                )

    def test_rejects_duplicate_or_empty_selected_role_track(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            melody_path = root / "melody.mid"
            duplicate_chord_path = root / "duplicate-chord.mid"
            empty_chord_path = root / "empty-chord.mid"
            melody_path.write_bytes(midi_file([track_chunk("melody", [(0, 480, 60)])]))
            duplicate_chord_path.write_bytes(
                midi_file(
                    [
                        track_chunk("chord", [(0, 480, 48), (0, 480, 52)]),
                        track_chunk("chord", [(0, 480, 55), (0, 480, 59)]),
                    ]
                )
            )
            empty_chord_path.write_bytes(
                midi_file([track_chunk("chord", []), track_chunk("other", [])])
            )

            with self.assertRaisesRegex(midi_to_json.MidiValidationError, "multiple non-empty tracks are named"):
                midi_to_json.convert_role_midis_to_json(
                    melody_path, duplicate_chord_path, None, root / "duplicate-output"
                )
            with self.assertRaisesRegex(midi_to_json.MidiValidationError, "no non-empty tracks"):
                midi_to_json.convert_role_midis_to_json(
                    melody_path, empty_chord_path, None, root / "empty-output"
                )

    def test_rejects_same_single_track_for_multiple_roles(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            melody_path = root / "melody.mid"
            shared_path = root / "shared.mid"
            melody_path.write_bytes(midi_file([track_chunk("melody", [(0, 480, 60)])]))
            shared_path.write_bytes(
                midi_file([track_chunk("shared", [(0, 480, 48), (0, 480, 52)])])
            )

            with self.assertRaisesRegex(midi_to_json.MidiValidationError, "same MIDI track"):
                midi_to_json.convert_role_midis_to_json(
                    melody_path, shared_path, shared_path, root / "output"
                )

    def test_role_mode_reuses_same_file_parse(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            melody_path = root / "melody.mid"
            shared_path = root / "shared.mid"
            melody_path.write_bytes(midi_file([track_chunk("melody", [(0, 480, 60)])]))
            shared_path.write_bytes(
                midi_file(
                    [
                        track_chunk("chord", [(0, 480, 48), (0, 480, 52)]),
                        track_chunk("control", [(0, 480, 2)]),
                    ]
                )
            )

            with patch.object(
                midi_to_json,
                "parse_midi_with_path",
                wraps=midi_to_json.parse_midi_with_path,
            ) as parse:
                midi_to_json.convert_role_midis_to_json(
                    melody_path, shared_path, shared_path, root / "output"
                )

        self.assertEqual(parse.call_count, 2)

    def test_main_rejects_incomplete_or_mixed_role_arguments(self) -> None:
        cases = [
            (["song.mid", "--melody", "melody.mid", "--chord", "chord.mid"], "cannot be used"),
            (["--melody", "melody.mid"], "requires both"),
            (["--control", "control.mid"], "requires both"),
            ([], "Provide input_midi"),
        ]
        for argv, message in cases:
            with self.subTest(argv=argv):
                stderr = io.StringIO()
                with redirect_stderr(stderr):
                    result = midi_to_json.main(argv)
                self.assertEqual(result, 2)
                self.assertIn(message, stderr.getvalue())

    def test_main_accepts_role_arguments(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            melody_path = root / "melody.mid"
            chord_path = root / "chord.mid"
            melody_path.write_bytes(midi_file([track_chunk("voice", [(0, 480, 60)])]))
            chord_path.write_bytes(
                midi_file([track_chunk("harmony", [(0, 480, 48), (0, 480, 52)])])
            )
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                result = midi_to_json.main(
                    [
                        "--melody",
                        str(melody_path),
                        "--chord",
                        str(chord_path),
                        "--output-root",
                        str(root / "output"),
                    ]
                )

        self.assertEqual(result, 0)
        self.assertIn("Wrote JSON files", stdout.getvalue())

    def test_main_rejects_unsafe_work_name_before_inspecting_outputs(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "song.mid"
            input_path.write_bytes(
                midi_file(
                    [
                        track_chunk("melody", [(0, 480, 60)]),
                        track_chunk("chord", [(0, 480, 48), (0, 480, 52)]),
                    ]
                )
            )
            stderr = io.StringIO()
            with (
                patch.object(midi_to_json, "existing_output_json_paths") as inspect_outputs,
                redirect_stderr(stderr),
            ):
                result = midi_to_json.main(
                    [
                        str(input_path),
                        "--work-name",
                        "../escape",
                        "--output-root",
                        str(root / "output"),
                    ]
                )

        self.assertEqual(result, 1)
        self.assertIn("work_name must be one safe directory name", stderr.getvalue())
        inspect_outputs.assert_not_called()

    def test_parses_and_concatenates_melody_lyrics(self) -> None:
        midi_bytes = midi_file(
            [
                track_chunk(
                    "melody",
                    [(0, 480, 60)],
                    [(0, 0x05, b"Hel"), (0, 0x05, b"lo")],
                ),
                track_chunk("chord", [(0, 480, 48), (0, 480, 52)]),
            ]
        )
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "song.mid"
            path.write_bytes(midi_bytes)
            output = midi_to_json.convert_midi_to_json(path, root / "output", 0.01)
            canonical = json.loads((output / "canonical.json").read_text(encoding="utf-8"))

        self.assertEqual(canonical["melody"][0]["lyric"], "Hello")

    def test_preserves_utf8_chinese_lyrics(self) -> None:
        midi_bytes = midi_file(
            [
                track_chunk(
                    "melody",
                    [(0, 480, 60)],
                    [(0, 0x05, "见面是最棒的事".encode("utf-8"))],
                ),
                track_chunk("chord", [(0, 480, 48), (0, 480, 52)]),
            ]
        )
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "song.mid"
            path.write_bytes(midi_bytes)
            output = midi_to_json.convert_midi_to_json(path, root / "output", 0.01)
            canonical = json.loads((output / "canonical.json").read_text(encoding="utf-8"))

        self.assertEqual(canonical["melody"][0]["lyric"], "见面是最棒的事")

    def test_requires_explicit_encoding_for_legacy_chinese_lyrics(self) -> None:
        midi_bytes = midi_file(
            [
                track_chunk(
                    "melody",
                    [(0, 480, 60)],
                    [(0, 0x05, "你好".encode("gb18030"))],
                ),
                track_chunk("chord", [(0, 480, 48), (0, 480, 52)]),
            ]
        )
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "song.mid"
            path.write_bytes(midi_bytes)
            with self.assertRaisesRegex(midi_to_json.MidiParseError, "not valid utf-8"):
                midi_to_json.convert_midi_to_json(path, root / "default", 0.01)
            output = midi_to_json.convert_midi_to_json(
                path, root / "explicit", 0.01, lyric_encoding="gb18030"
            )
            canonical = json.loads((output / "canonical.json").read_text(encoding="utf-8"))

        self.assertEqual(canonical["melody"][0]["lyric"], "你好")

    def test_rejects_orphan_melody_lyric(self) -> None:
        midi_bytes = midi_file(
            [
                track_chunk("melody", [(0, 480, 60)], [(240, 0x05, b"orphan")]),
                track_chunk("chord", [(0, 480, 48), (0, 480, 52)]),
            ]
        )
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "song.mid"
            path.write_bytes(midi_bytes)
            with self.assertRaisesRegex(midi_to_json.MidiValidationError, "does not match"):
                midi_to_json.convert_midi_to_json(path, root / "output", 0.01)


if __name__ == "__main__":
    unittest.main()
