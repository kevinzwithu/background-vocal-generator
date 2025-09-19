#!/usr/bin/env python3
"""Convert a melody/chord MIDI file into project canonical JSON.

The parser intentionally uses only the Python standard library so the first
project phase can run without installing dependencies.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


C0_MIDI = 12
C6_MIDI = 84
PITCH_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


class MidiParseError(Exception):
    pass


class MidiValidationError(Exception):
    pass


@dataclass(frozen=True)
class RawNote:
    track_name: str
    midi: int
    start_ticks: int
    end_ticks: int


@dataclass(frozen=True)
class TrackData:
    name: str | None
    notes: list[RawNote]


@dataclass(frozen=True)
class TempoEvent:
    ticks: int
    bpm: int
    order: int


@dataclass(frozen=True)
class TimeSignatureEvent:
    ticks: int
    numerator: int
    denominator: int
    order: int


@dataclass(frozen=True)
class TimeSignatureSegment:
    start: float
    numerator: int
    denominator: int
    bar_length: float
    grid_size: float
    start_bar: int


def read_exact(data: bytes, offset: int, size: int, label: str) -> tuple[bytes, int]:
    end = offset + size
    if end > len(data):
        raise MidiParseError(f"Unexpected end of file while reading {label}.")
    return data[offset:end], end


def read_u16(data: bytes, offset: int) -> tuple[int, int]:
    raw, offset = read_exact(data, offset, 2, "uint16")
    return int.from_bytes(raw, "big"), offset


def read_u32(data: bytes, offset: int) -> tuple[int, int]:
    raw, offset = read_exact(data, offset, 4, "uint32")
    return int.from_bytes(raw, "big"), offset


def read_var_len(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    for _ in range(4):
        if offset >= len(data):
            raise MidiParseError("Unexpected end of track while reading variable length value.")
        byte = data[offset]
        offset += 1
        value = (value << 7) | (byte & 0x7F)
        if byte < 0x80:
            return value, offset
    raise MidiParseError("Invalid variable length value longer than 4 bytes.")


def decode_text(raw: bytes) -> str:
    for encoding in ("utf-8", "latin-1"):
        try:
            return raw.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace").strip()


def midi_name(midi: int) -> str:
    octave = midi // 12 - 1
    return f"{PITCH_NAMES[midi % 12]}{octave}"


def normalize_number(value: float, digits: int = 6) -> int | float:
    if abs(value - round(value)) < 1e-9:
        return int(round(value))
    return round(value, digits)


def close_enough_to_grid(value: float, grid_size: float, tolerance: float) -> tuple[bool, float]:
    nearest_index = round(value / grid_size)
    nearest = nearest_index * grid_size
    return abs(value - nearest) <= tolerance, nearest


def parse_track(
    data: bytes,
    ticks_per_beat: int,
    track_index: int,
    event_order_start: int,
) -> tuple[TrackData, list[TempoEvent], list[TimeSignatureEvent], int]:
    del ticks_per_beat

    offset = 0
    abs_ticks = 0
    running_status: int | None = None
    track_name: str | None = None
    notes: list[RawNote] = []
    tempo_events: list[TempoEvent] = []
    time_signature_events: list[TimeSignatureEvent] = []
    active_notes: dict[tuple[int, int], list[int]] = {}
    event_order = event_order_start

    while offset < len(data):
        delta, offset = read_var_len(data, offset)
        abs_ticks += delta

        if offset >= len(data):
            raise MidiParseError(f"Track {track_index}: missing event status byte.")

        status = data[offset]
        if status < 0x80:
            if running_status is None:
                raise MidiParseError(f"Track {track_index}: running status used before status byte.")
            status = running_status
        else:
            offset += 1
            if 0x80 <= status <= 0xEF:
                running_status = status
            else:
                running_status = None

        if status == 0xFF:
            meta_type_raw, offset = read_exact(data, offset, 1, "meta type")
            meta_type = meta_type_raw[0]
            length, offset = read_var_len(data, offset)
            payload, offset = read_exact(data, offset, length, "meta payload")

            if meta_type == 0x03:
                track_name = decode_text(payload)
            elif meta_type == 0x51 and length == 3:
                microseconds_per_quarter = int.from_bytes(payload, "big")
                if microseconds_per_quarter <= 0:
                    raise MidiParseError(f"Track {track_index}: invalid tempo event.")
                bpm = math.floor(60_000_000 / microseconds_per_quarter)
                tempo_events.append(TempoEvent(abs_ticks, bpm, event_order))
            elif meta_type == 0x58 and length >= 2:
                numerator = payload[0]
                denominator = 2 ** payload[1]
                if numerator <= 0 or denominator <= 0:
                    raise MidiParseError(f"Track {track_index}: invalid time signature event.")
                time_signature_events.append(
                    TimeSignatureEvent(abs_ticks, numerator, denominator, event_order)
                )
            event_order += 1
            continue

        if status in (0xF0, 0xF7):
            length, offset = read_var_len(data, offset)
            _, offset = read_exact(data, offset, length, "sysex payload")
            event_order += 1
            continue

        if not 0x80 <= status <= 0xEF:
            raise MidiParseError(f"Track {track_index}: unsupported status byte 0x{status:02X}.")

        message_type = status & 0xF0
        channel = status & 0x0F
        param_count = 1 if message_type in (0xC0, 0xD0) else 2
        params, offset = read_exact(data, offset, param_count, "MIDI event parameters")

        if message_type in (0x80, 0x90):
            note = params[0]
            velocity = params[1]
            key = (channel, note)
            is_note_on = message_type == 0x90 and velocity > 0
            if is_note_on:
                active_notes.setdefault(key, []).append(abs_ticks)
            else:
                starts = active_notes.get(key)
                if starts:
                    start_ticks = starts.pop(0)
                    if not starts:
                        del active_notes[key]
                    display_name = track_name or f"track_{track_index}"
                    notes.append(RawNote(display_name, note, start_ticks, abs_ticks))
        event_order += 1

    if active_notes:
        pending = []
        for (_, note), starts in active_notes.items():
            for start in starts:
                pending.append(f"{midi_name(note)} at tick {start}")
        joined = ", ".join(pending[:5])
        raise MidiParseError(f"Track {track_index}: unclosed note(s): {joined}.")

    return TrackData(track_name, notes), tempo_events, time_signature_events, event_order


def parse_midi(path: Path) -> tuple[int, list[TrackData], list[TempoEvent], list[TimeSignatureEvent]]:
    data = path.read_bytes()
    offset = 0

    chunk_type, offset = read_exact(data, offset, 4, "MIDI header chunk type")
    if chunk_type != b"MThd":
        raise MidiParseError("File does not start with MThd header.")

    header_length, offset = read_u32(data, offset)
    header_payload, offset = read_exact(data, offset, header_length, "MIDI header")
    if header_length < 6:
        raise MidiParseError("MIDI header is shorter than 6 bytes.")

    midi_format = int.from_bytes(header_payload[0:2], "big")
    track_count = int.from_bytes(header_payload[2:4], "big")
    division = int.from_bytes(header_payload[4:6], "big")
    if division & 0x8000:
        raise MidiParseError("SMPTE time division is not supported. Use ticks-per-quarter MIDI.")
    ticks_per_beat = division
    if ticks_per_beat <= 0:
        raise MidiParseError("Invalid ticks-per-quarter division.")
    if midi_format not in (0, 1, 2):
        raise MidiParseError(f"Unsupported MIDI format: {midi_format}.")

    tracks: list[TrackData] = []
    tempo_events: list[TempoEvent] = []
    time_signature_events: list[TimeSignatureEvent] = []
    event_order = 0

    for track_index in range(track_count):
        chunk_type, offset = read_exact(data, offset, 4, f"track {track_index} chunk type")
        if chunk_type != b"MTrk":
            raise MidiParseError(f"Expected MTrk chunk for track {track_index}.")
        track_length, offset = read_u32(data, offset)
        track_payload, offset = read_exact(data, offset, track_length, f"track {track_index}")
        track, track_tempos, track_sigs, event_order = parse_track(
            track_payload,
            ticks_per_beat,
            track_index,
            event_order,
        )
        tracks.append(track)
        tempo_events.extend(track_tempos)
        time_signature_events.extend(track_sigs)

    return ticks_per_beat, tracks, tempo_events, time_signature_events


def ticks_to_beats(ticks: int, ticks_per_beat: int) -> float:
    return ticks / ticks_per_beat


def normalize_tempo_map(events: list[TempoEvent], ticks_per_beat: int) -> list[dict[str, Any]]:
    by_time: dict[float, tuple[int, int]] = {}
    for event in sorted(events, key=lambda item: (item.ticks, item.order)):
        by_time[ticks_to_beats(event.ticks, ticks_per_beat)] = (event.bpm, event.order)

    if 0.0 not in by_time:
        by_time[0.0] = (120, -1)

    return [
        {"time": normalize_number(time), "bpm": bpm}
        for time, (bpm, _) in sorted(by_time.items(), key=lambda item: item[0])
    ]


def normalize_time_signature_map(
    events: list[TimeSignatureEvent],
    ticks_per_beat: int,
) -> list[dict[str, Any]]:
    by_time: dict[float, tuple[int, int, int]] = {}
    for event in sorted(events, key=lambda item: (item.ticks, item.order)):
        by_time[ticks_to_beats(event.ticks, ticks_per_beat)] = (
            event.numerator,
            event.denominator,
            event.order,
        )

    if 0.0 not in by_time:
        by_time[0.0] = (4, 4, -1)

    return [
        {
            "time": normalize_number(time),
            "numerator": numerator,
            "denominator": denominator,
        }
        for time, (numerator, denominator, _) in sorted(by_time.items(), key=lambda item: item[0])
    ]


def build_time_signature_segments(
    time_signature_map: list[dict[str, Any]],
) -> list[TimeSignatureSegment]:
    segments: list[TimeSignatureSegment] = []
    current_start_bar = 1

    for index, item in enumerate(time_signature_map):
        start = float(item["time"])
        numerator = int(item["numerator"])
        denominator = int(item["denominator"])
        bar_length = numerator * 4 / denominator
        grid_size = bar_length / 16

        if index > 0:
            previous = segments[-1]
            elapsed = start - previous.start
            bars_elapsed = elapsed / previous.bar_length
            rounded = round(bars_elapsed)
            if abs(bars_elapsed - rounded) > 1e-6:
                raise MidiValidationError(
                    "Time signature change is not aligned to the previous bar boundary: "
                    f"time={normalize_number(start)} beats."
                )
            current_start_bar = previous.start_bar + rounded

        segments.append(
            TimeSignatureSegment(
                start=start,
                numerator=numerator,
                denominator=denominator,
                bar_length=bar_length,
                grid_size=grid_size,
                start_bar=current_start_bar,
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


def quantization_details(
    segments: list[TimeSignatureSegment],
    time: float,
    tolerance: float,
) -> tuple[bool, TimeSignatureSegment, float]:
    segment = segment_for_time(segments, time)
    offset = time - segment.start
    ok, nearest_offset = close_enough_to_grid(offset, segment.grid_size, tolerance)
    return ok, segment, segment.start + nearest_offset


def bar_position(segments: list[TimeSignatureSegment], time: float) -> tuple[int, int | float, int]:
    segment = segment_for_time(segments, time)
    offset = max(0.0, time - segment.start)
    bar_offset = math.floor((offset + 1e-9) / segment.bar_length)
    bar_start = segment.start + bar_offset * segment.bar_length
    offset_in_bar = max(0.0, time - bar_start)
    beat = 1 + offset_in_bar
    grid = round(offset_in_bar / segment.grid_size)
    if grid >= 16:
        grid = 15
    return segment.start_bar + bar_offset, normalize_number(beat), grid


def select_named_track(tracks: list[TrackData], expected_name: str) -> TrackData:
    matches = [
        track
        for track in tracks
        if track.name is not None and track.name.strip().lower() == expected_name.lower()
    ]
    if not matches:
        available = ", ".join(track.name or "<unnamed>" for track in tracks)
        raise MidiValidationError(
            f"Missing required MIDI track '{expected_name}'. Available tracks: {available}"
        )
    if len(matches) > 1:
        raise MidiValidationError(f"Multiple MIDI tracks named '{expected_name}' were found.")
    return matches[0]


def convert_notes(
    raw_notes: list[RawNote],
    ticks_per_beat: int,
    segments: list[TimeSignatureSegment],
    tolerance: float,
    track_label: str,
) -> tuple[list[dict[str, Any]], int]:
    output: list[dict[str, Any]] = []
    filtered = 0

    for raw in sorted(raw_notes, key=lambda item: (item.start_ticks, item.end_ticks, item.midi)):
        if raw.end_ticks <= raw.start_ticks:
            raise MidiValidationError(
                f"{track_label}: note {midi_name(raw.midi)} midi={raw.midi} has non-positive duration."
            )

        if raw.midi < C0_MIDI or raw.midi > C6_MIDI:
            filtered += 1
            continue

        start = ticks_to_beats(raw.start_ticks, ticks_per_beat)
        end = ticks_to_beats(raw.end_ticks, ticks_per_beat)
        duration = end - start

        for label, value in (("start", start), ("end", end)):
            ok, segment, nearest = quantization_details(segments, value, tolerance)
            if not ok:
                raise MidiValidationError(
                    f"{track_label}: unquantized {label} for {midi_name(raw.midi)} midi={raw.midi}. "
                    f"time={normalize_number(value)} beats, "
                    f"nearest={normalize_number(nearest)} beats, "
                    f"timeSignature={segment.numerator}/{segment.denominator}, "
                    f"grid={normalize_number(segment.grid_size)} beats, "
                    f"tolerance={tolerance} beats."
                )

        bar, beat, grid = bar_position(segments, start)
        output.append(
            {
                "name": midi_name(raw.midi),
                "midi": raw.midi,
                "time": normalize_number(start),
                "duration": normalize_number(duration),
                "bar": bar,
                "beat": beat,
                "grid": grid,
            }
        )

    return output, filtered


def validate_chord_alignment(notes: list[dict[str, Any]], tolerance: float) -> None:
    groups: dict[float, list[dict[str, Any]]] = {}
    for note in notes:
        start = float(note["time"])
        key = round(start / max(tolerance, 1e-9)) * max(tolerance, 1e-9)
        groups.setdefault(key, []).append(note)

    for group in groups.values():
        if len(group) <= 1:
            continue
        expected_end = float(group[0]["time"]) + float(group[0]["duration"])
        for note in group[1:]:
            end = float(note["time"]) + float(note["duration"])
            if abs(end - expected_end) > tolerance:
                names = ", ".join(f"{item['name']}@{item['time']}+{item['duration']}" for item in group)
                raise MidiValidationError(
                    "Chord notes with the same start time must end together. "
                    f"Problem group: {names}"
                )


def build_header(
    melody_notes: list[dict[str, Any]],
    chord_notes: list[dict[str, Any]],
    tempo_map: list[dict[str, Any]],
    time_signature_map: list[dict[str, Any]],
) -> dict[str, Any]:
    duration = 0.0
    for note in melody_notes + chord_notes:
        duration = max(duration, float(note["time"]) + float(note["duration"]))

    return {
        "startTime": 0,
        "duration": normalize_number(duration),
        "timeUnit": "beats",
        "grid": "1/16_bar",
        "tempoMap": tempo_map,
        "timeSignatureMap": time_signature_map,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def convert_midi_to_json(input_path: Path, output_root: Path, tolerance: float) -> Path:
    ticks_per_beat, tracks, tempo_events, time_signature_events = parse_midi(input_path)

    melody_track = select_named_track(tracks, "melody")
    chord_track = select_named_track(tracks, "chord")

    tempo_map = normalize_tempo_map(tempo_events, ticks_per_beat)
    time_signature_map = normalize_time_signature_map(time_signature_events, ticks_per_beat)
    segments = build_time_signature_segments(time_signature_map)

    melody_notes, filtered_melody = convert_notes(
        melody_track.notes,
        ticks_per_beat,
        segments,
        tolerance,
        "melody",
    )
    chord_notes, filtered_chord = convert_notes(
        chord_track.notes,
        ticks_per_beat,
        segments,
        tolerance,
        "chord",
    )
    validate_chord_alignment(chord_notes, tolerance)

    header = build_header(melody_notes, chord_notes, tempo_map, time_signature_map)
    canonical = {"header": header, "melody": melody_notes, "chord": chord_notes}

    work_name = input_path.stem
    output_dir = output_root / work_name
    output_dir.mkdir(parents=True, exist_ok=True)

    write_json(output_dir / "canonical.json", canonical)
    write_json(output_dir / "melody.json", {"header": header, "melody": melody_notes})
    write_json(output_dir / "chord.json", {"header": header, "chord": chord_notes})

    if filtered_melody or filtered_chord:
        print(
            "Filtered out-of-range notes: "
            f"melody={filtered_melody}, chord={filtered_chord}",
            file=sys.stderr,
        )

    return output_dir


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert a melody/chord MIDI file to canonical JSON."
    )
    parser.add_argument("input_midi", help="Input MIDI file containing melody and chord tracks.")
    parser.add_argument(
        "--output-root",
        default="output",
        help="Output root directory. Default: output",
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
    input_path = Path(args.input_midi).expanduser()
    output_root = Path(args.output_root).expanduser()

    if not input_path.exists():
        print(f"ERROR: Input MIDI file does not exist: {input_path}", file=sys.stderr)
        return 2

    try:
        output_dir = convert_midi_to_json(input_path, output_root, args.tolerance)
    except (MidiParseError, MidiValidationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote JSON files to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

