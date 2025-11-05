#!/usr/bin/env python3
"""Convert project bv_raw.json into a Standard MIDI file."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_TICKS_PER_BEAT = 480
C0_MIDI = 12
C6_MIDI = 84
EXPECTED_TRACKS = (
    "LeadDouble",
    "HighHarmonyA",
    "HighHarmonyB",
    "LowHarmony",
    "VocalPad1",
    "VocalPad2",
    "VocalPad3",
)


class BvMidiError(Exception):
    pass


@dataclass(frozen=True)
class TimeSignatureSegment:
    start: float
    numerator: int
    denominator: int
    bar_length: float
    grid_size: float


def normalize_number(value: float, digits: int = 6) -> int | float:
    if abs(value - round(value)) < 1e-9:
        return int(round(value))
    return round(value, digits)


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BvMidiError(f"Invalid JSON file {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise BvMidiError(f"JSON root must be an object: {path}")
    return payload


def sibling_header(bv_path: Path) -> dict[str, Any]:
    for name in ("canonical.json", "melody.json"):
        candidate = bv_path.parent / name
        if candidate.exists():
            payload = read_json(candidate)
            header = payload.get("header")
            if isinstance(header, dict):
                return header
    return {}


def resolve_header(payload: dict[str, Any], bv_path: Path) -> dict[str, Any]:
    header: dict[str, Any] = {}
    header.update(sibling_header(bv_path))

    raw_header = payload.get("header")
    if isinstance(raw_header, dict):
        header.update(raw_header)

    header.setdefault("startTime", 0)
    header.setdefault("duration", 0)
    header.setdefault("timeUnit", "beats")
    header.setdefault("grid", "1/16_bar")
    header.setdefault("tempoMap", [{"time": 0, "bpm": 120}])
    header.setdefault("timeSignatureMap", [{"time": 0, "numerator": 4, "denominator": 4}])

    if header["timeUnit"] != "beats":
        raise BvMidiError("header.timeUnit must be 'beats'.")
    if header["grid"] != "1/16_bar":
        raise BvMidiError("header.grid must be '1/16_bar'.")

    validate_tempo_map(header["tempoMap"])
    validate_time_signature_map(header["timeSignatureMap"])
    return header


def validate_tempo_map(tempo_map: Any) -> None:
    if not isinstance(tempo_map, list) or not tempo_map:
        raise BvMidiError("header.tempoMap must be a non-empty list.")
    previous_time = -1.0
    for item in tempo_map:
        if not isinstance(item, dict):
            raise BvMidiError("Each tempoMap item must be an object.")
        time = float(item.get("time"))
        bpm = int(item.get("bpm"))
        if time < 0:
            raise BvMidiError("tempoMap time cannot be negative.")
        if time < previous_time:
            raise BvMidiError("tempoMap must be sorted by time.")
        if bpm <= 0:
            raise BvMidiError("tempoMap bpm must be positive.")
        previous_time = time


def validate_time_signature_map(time_signature_map: Any) -> None:
    if not isinstance(time_signature_map, list) or not time_signature_map:
        raise BvMidiError("header.timeSignatureMap must be a non-empty list.")
    previous_time = -1.0
    for item in time_signature_map:
        if not isinstance(item, dict):
            raise BvMidiError("Each timeSignatureMap item must be an object.")
        time = float(item.get("time"))
        numerator = int(item.get("numerator"))
        denominator = int(item.get("denominator"))
        if time < 0:
            raise BvMidiError("timeSignatureMap time cannot be negative.")
        if time < previous_time:
            raise BvMidiError("timeSignatureMap must be sorted by time.")
        if numerator <= 0:
            raise BvMidiError("timeSignatureMap numerator must be positive.")
        if denominator <= 0 or denominator & (denominator - 1):
            raise BvMidiError("timeSignatureMap denominator must be a power of two.")
        previous_time = time


def build_time_signature_segments(header: dict[str, Any]) -> list[TimeSignatureSegment]:
    segments: list[TimeSignatureSegment] = []
    for item in header["timeSignatureMap"]:
        start = float(item["time"])
        numerator = int(item["numerator"])
        denominator = int(item["denominator"])
        bar_length = numerator * 4 / denominator
        segments.append(
            TimeSignatureSegment(
                start=start,
                numerator=numerator,
                denominator=denominator,
                bar_length=bar_length,
                grid_size=bar_length / 16,
            )
        )
    return segments


def segment_for_time(segments: list[TimeSignatureSegment], time: float) -> TimeSignatureSegment:
    selected = segments[0]
    for segment in segments:
        if time + 1e-9 >= segment.start:
            selected = segment
        else:
            break
    return selected


def quantized(
    segments: list[TimeSignatureSegment],
    time: float,
    tolerance: float,
) -> tuple[bool, float, TimeSignatureSegment]:
    segment = segment_for_time(segments, time)
    offset = time - segment.start
    nearest_index = round(offset / segment.grid_size)
    nearest = segment.start + nearest_index * segment.grid_size
    return abs(time - nearest) <= tolerance, nearest, segment


def load_tracks(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    tracks = payload.get("tracks")
    if not isinstance(tracks, dict):
        raise BvMidiError("bv_raw.json must contain a 'tracks' object.")

    missing = [name for name in EXPECTED_TRACKS if name not in tracks]
    extra = [name for name in tracks if name not in EXPECTED_TRACKS]
    if missing:
        raise BvMidiError(f"Missing BV track(s): {', '.join(missing)}")
    if extra:
        raise BvMidiError(f"Unexpected BV track(s): {', '.join(extra)}")

    normalized: dict[str, list[dict[str, Any]]] = {}
    for track_name in EXPECTED_TRACKS:
        notes = tracks[track_name]
        if not isinstance(notes, list):
            raise BvMidiError(f"Track {track_name} must be a list.")
        normalized[track_name] = notes
    return normalized


def validate_notes(
    tracks: dict[str, list[dict[str, Any]]],
    segments: list[TimeSignatureSegment],
    tolerance: float,
) -> float:
    max_end = 0.0
    for track_name, notes in tracks.items():
        by_pitch: dict[int, list[tuple[float, float, dict[str, Any]]]] = {}
        for note in notes:
            if not isinstance(note, dict):
                raise BvMidiError(f"Track {track_name} contains a non-object note.")
            for key in ("name", "midi", "time", "duration"):
                if key not in note:
                    raise BvMidiError(f"Track {track_name} note is missing '{key}'.")

            midi = int(note["midi"])
            start = float(note["time"])
            duration = float(note["duration"])
            end = start + duration

            if midi < C0_MIDI or midi > C6_MIDI:
                raise BvMidiError(
                    f"Track {track_name} note {note.get('name')} midi={midi} is outside C0-C6."
                )
            if start < 0:
                raise BvMidiError(f"Track {track_name} note {note.get('name')} has negative time.")
            if duration <= 0:
                raise BvMidiError(
                    f"Track {track_name} note {note.get('name')} has non-positive duration."
                )

            for label, value in (("start", start), ("end", end)):
                ok, nearest, segment = quantized(segments, value, tolerance)
                if not ok:
                    raise BvMidiError(
                        f"Track {track_name} note {note.get('name')} has unquantized {label}. "
                        f"time={normalize_number(value)} beats, "
                        f"nearest={normalize_number(nearest)} beats, "
                        f"timeSignature={segment.numerator}/{segment.denominator}, "
                        f"grid={normalize_number(segment.grid_size)} beats."
                    )

            by_pitch.setdefault(midi, []).append((start, end, note))
            max_end = max(max_end, end)

        for midi, pitch_notes in by_pitch.items():
            previous_end = -1.0
            previous_note: dict[str, Any] | None = None
            for start, end, note in sorted(pitch_notes, key=lambda item: (item[0], item[1])):
                if start < previous_end - tolerance:
                    raise BvMidiError(
                        f"Track {track_name} has overlapping same-pitch notes for midi={midi}: "
                        f"{previous_note} overlaps {note}."
                    )
                previous_end = max(previous_end, end)
                previous_note = note
    return max_end


def vlq(value: int) -> bytes:
    if value < 0:
        raise BvMidiError(f"Cannot write negative delta time: {value}")
    parts = [value & 0x7F]
    value >>= 7
    while value:
        parts.insert(0, (value & 0x7F) | 0x80)
        value >>= 7
    return bytes(parts)


def meta_event(meta_type: int, payload: bytes) -> bytes:
    return bytes([0xFF, meta_type]) + vlq(len(payload)) + payload


def track_chunk(events: list[tuple[int, int, bytes]], end_tick: int) -> bytes:
    payload = bytearray()
    previous_tick = 0

    sorted_events = sorted(events, key=lambda item: (item[0], item[1]))
    for tick, _, event_payload in sorted_events:
        payload.extend(vlq(tick - previous_tick))
        payload.extend(event_payload)
        previous_tick = tick

    payload.extend(vlq(max(0, end_tick - previous_tick)))
    payload.extend(meta_event(0x2F, b""))
    return b"MTrk" + len(payload).to_bytes(4, "big") + bytes(payload)


def beats_to_ticks(beats: float, ticks_per_beat: int) -> int:
    return round(beats * ticks_per_beat)


def conductor_track(header: dict[str, Any], ticks_per_beat: int, end_tick: int) -> bytes:
    events: list[tuple[int, int, bytes]] = []
    events.append((0, 0, meta_event(0x03, b"conductor")))

    for item in header["tempoMap"]:
        tick = beats_to_ticks(float(item["time"]), ticks_per_beat)
        bpm = int(item["bpm"])
        microseconds_per_quarter = round(60_000_000 / bpm)
        events.append((tick, 1, meta_event(0x51, microseconds_per_quarter.to_bytes(3, "big"))))

    for item in header["timeSignatureMap"]:
        tick = beats_to_ticks(float(item["time"]), ticks_per_beat)
        numerator = int(item["numerator"])
        denominator = int(item["denominator"])
        denominator_power = int(math.log2(denominator))
        events.append(
            (
                tick,
                2,
                meta_event(0x58, bytes([numerator, denominator_power, 24, 8])),
            )
        )

    return track_chunk(events, end_tick)


def note_track(
    track_name: str,
    notes: list[dict[str, Any]],
    ticks_per_beat: int,
    end_tick: int,
    velocity: int,
) -> bytes:
    events: list[tuple[int, int, bytes]] = [(0, 0, meta_event(0x03, track_name.encode("utf-8")))]

    for note in notes:
        midi = int(note["midi"])
        start_tick = beats_to_ticks(float(note["time"]), ticks_per_beat)
        end_note_tick = beats_to_ticks(float(note["time"]) + float(note["duration"]), ticks_per_beat)
        events.append((end_note_tick, 1, bytes([0x80, midi, 0])))
        events.append((start_tick, 2, bytes([0x90, midi, velocity])))

    return track_chunk(events, end_tick)


def write_midi(
    path: Path,
    header: dict[str, Any],
    tracks: dict[str, list[dict[str, Any]]],
    ticks_per_beat: int,
    velocity: int,
    max_note_end: float,
) -> None:
    header_duration = float(header.get("duration", 0) or 0)
    end_beats = max(header_duration, max_note_end)
    for item in header["tempoMap"]:
        end_beats = max(end_beats, float(item["time"]))
    for item in header["timeSignatureMap"]:
        end_beats = max(end_beats, float(item["time"]))
    end_tick = beats_to_ticks(end_beats, ticks_per_beat)

    chunks = [conductor_track(header, ticks_per_beat, end_tick)]
    for track_name in EXPECTED_TRACKS:
        chunks.append(note_track(track_name, tracks[track_name], ticks_per_beat, end_tick, velocity))

    midi_header_payload = (
        (1).to_bytes(2, "big")
        + len(chunks).to_bytes(2, "big")
        + ticks_per_beat.to_bytes(2, "big")
    )
    midi_header = b"MThd" + len(midi_header_payload).to_bytes(4, "big") + midi_header_payload

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(midi_header + b"".join(chunks))


def convert_bv_raw_to_midi(
    bv_path: Path,
    output_path: Path | None = None,
    ticks_per_beat: int = DEFAULT_TICKS_PER_BEAT,
    velocity: int = 80,
    tolerance: float = 0.01,
) -> Path:
    payload = read_json(bv_path)
    header = resolve_header(payload, bv_path)
    tracks = load_tracks(payload)
    segments = build_time_signature_segments(header)
    max_note_end = validate_notes(tracks, segments, tolerance)

    destination = output_path or (bv_path.parent / "bv.mid")
    write_midi(destination, header, tracks, ticks_per_beat, velocity, max_note_end)
    return destination


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert bv_raw.json to MIDI.")
    parser.add_argument("bv_raw_json", help="Path to output/<work_name>/bv_raw.json.")
    parser.add_argument("-o", "--output", help="Output MIDI path. Default: sibling bv.mid.")
    parser.add_argument(
        "--ticks-per-beat",
        type=int,
        default=DEFAULT_TICKS_PER_BEAT,
        help=f"MIDI ticks per beat. Default: {DEFAULT_TICKS_PER_BEAT}",
    )
    parser.add_argument(
        "--velocity",
        type=int,
        default=80,
        help="MIDI note velocity to write. Default: 80",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.01,
        help="Quantization tolerance in beats. Default: 0.01",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    bv_path = Path(args.bv_raw_json).expanduser()
    output_path = Path(args.output).expanduser() if args.output else None

    if not bv_path.exists():
        print(f"ERROR: bv_raw.json does not exist: {bv_path}", file=sys.stderr)
        return 2
    if args.ticks_per_beat <= 0:
        print("ERROR: --ticks-per-beat must be positive.", file=sys.stderr)
        return 2
    if not 1 <= args.velocity <= 127:
        print("ERROR: --velocity must be between 1 and 127.", file=sys.stderr)
        return 2

    try:
        destination = convert_bv_raw_to_midi(
            bv_path,
            output_path,
            args.ticks_per_beat,
            args.velocity,
            args.tolerance,
        )
    except BvMidiError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote MIDI to {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

