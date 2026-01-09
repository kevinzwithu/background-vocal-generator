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


def midi_file(tracks: list[bytes], midi_format: int = 1) -> bytes:
    header_payload = (
        midi_format.to_bytes(2, "big")
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
                    "grid": 8,
                },
                {
                    "section": "no_bv",
                    "name": "E-1",
                    "midi": 4,
                    "time": 3,
                    "duration": 1,
                    "bar": 1,
                    "beat": 4,
                    "grid": 12,
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
                    "grid": 8,
                },
                {
                    "section": "no_bv",
                    "name": "E0",
                    "midi": 16,
                    "time": 3,
                    "duration": 1,
                    "bar": 1,
                    "beat": 4,
                    "grid": 12,
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
                {"name": "C3", "midi": 48, "time": 2, "duration": 1, "bar": 1, "beat": 3, "grid": 8},
                {"name": "E3", "midi": 52, "time": 2, "duration": 1, "bar": 1, "beat": 3, "grid": 8},
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

    def test_rejects_when_midi_exceeds_8_minutes(self) -> None:
        midi_bytes = midi_file(
            [
                track_chunk("melody", [(0, TICKS_PER_BEAT * 4 * 249, 60)]),
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
            with self.assertRaisesRegex(midi_to_json.MidiValidationError, "8 minutes"):
                midi_to_json.convert_midi_to_json(input_path, root / "output", 0.01)

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


if __name__ == "__main__":
    unittest.main()
