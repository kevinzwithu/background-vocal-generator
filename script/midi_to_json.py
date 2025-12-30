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
MAX_BARS = 250
MAX_SECONDS = 8 * 60
MAX_MIDI_FILE_BYTES = 5 * 1024 * 1024
MAX_TRACKS = 64
MAX_MIDI_EVENTS = 200_000
MAX_PARSED_NOTES = 50_000
MAX_TEMPO_EVENTS = 2_000
MAX_TIME_SIGNATURE_EVENTS = 1_000
OUTPUT_JSON_FILENAMES = ("canonical.json", "melody.json", "chord.json")
PITCH_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
CONTROL_SECTIONS = {
    0: "verse",
    1: "pre_chorus",
    2: "chorus",
    3: "bridge",
    4: "no_bv",
}
CONTROL_OCTAVE_BASES = (0, 12)


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
    bpm: int | float
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


def quarter_bpm_from_microseconds(microseconds_per_quarter: int) -> int | float:
    bpm = 60_000_000 / microseconds_per_quarter
    quarter_bpm = round(bpm * 4) / 4
    if abs(bpm - quarter_bpm) > 0.001:
        raise MidiParseError(
            "Tempo event BPM must use 0.25 BPM increments. "
            f"Found approximately {round(bpm, 6)} BPM."
        )
    return normalize_number(quarter_bpm)


def reject_if_over_limit(value: int, limit: int, label: str) -> None:
    if value > limit:
        raise MidiParseError(f"MIDI {label} exceeds limit of {limit}.")


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
        reject_if_over_limit(event_order + 1, MAX_MIDI_EVENTS, "event count")

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
                bpm = quarter_bpm_from_microseconds(microseconds_per_quarter)
                tempo_events.append(TempoEvent(abs_ticks, bpm, event_order))
                reject_if_over_limit(len(tempo_events), MAX_TEMPO_EVENTS, "tempo event count")
            elif meta_type == 0x58 and length >= 2:
                numerator = payload[0]
                denominator = 2 ** payload[1]
                if numerator <= 0 or denominator <= 0:
                    raise MidiParseError(f"Track {track_index}: invalid time signature event.")
                time_signature_events.append(
                    TimeSignatureEvent(abs_ticks, numerator, denominator, event_order)
                )
                reject_if_over_limit(
                    len(time_signature_events),
                    MAX_TIME_SIGNATURE_EVENTS,
                    "time signature event count",
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
                    reject_if_over_limit(len(notes), MAX_PARSED_NOTES, "parsed note count")
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
    try:
        file_size = path.stat().st_size
    except OSError as exc:
        raise MidiParseError(f"Unable to inspect MIDI file: {exc}") from exc
    reject_if_over_limit(file_size, MAX_MIDI_FILE_BYTES, "file size")

    try:
        data = path.read_bytes()
    except OSError as exc:
        raise MidiParseError(f"Unable to read MIDI file: {exc}") from exc
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
    reject_if_over_limit(track_count, MAX_TRACKS, "track count")
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
    by_time: dict[float, tuple[int | float, int]] = {}
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


def non_empty_tracks(tracks: list[TrackData]) -> list[TrackData]:
    return [track for track in tracks if track.notes]


def validate_required_track_set(tracks: list[TrackData]) -> None:
    available = ", ".join(track.name or "<unnamed>" for track in tracks)
    normalized_names = [
        track.name.strip().lower()
        for track in tracks
        if track.name is not None
    ]

    if normalized_names.count("melody") != 1 or normalized_names.count("chord") != 1:
        raise MidiValidationError(
            "MIDI must contain exactly one non-empty track named 'melody' and exactly one "
            "non-empty track named 'chord'. "
            f"Found {len(tracks)} non-empty track(s): {available}"
        )
    if normalized_names.count("control") > 1:
        raise MidiValidationError("MIDI must not contain more than one non-empty 'control' track.")


def optional_named_track(tracks: list[TrackData], expected_name: str) -> TrackData | None:
    matches = [
        track
        for track in tracks
        if track.name is not None and track.name.strip().lower() == expected_name.lower()
    ]
    return matches[0] if matches else None


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


def convert_control_notes(
    raw_notes: list[RawNote],
    ticks_per_beat: int,
    segments: list[TimeSignatureSegment],
    duration: float,
    tolerance: float,
) -> list[dict[str, Any]]:
    starts: list[tuple[float, RawNote]] = []
    intervals: list[tuple[float, float, RawNote]] = []
    for raw in sorted(raw_notes, key=lambda item: (item.start_ticks, item.midi)):
        section = CONTROL_SECTIONS.get(raw.midi % 12)
        if section is None or raw.midi - raw.midi % 12 not in CONTROL_OCTAVE_BASES:
            raise MidiValidationError(
                "control: only C/C#/D/D#/E at MIDI 0-4 or 12-16 are supported."
            )
        start = ticks_to_beats(raw.start_ticks, ticks_per_beat)
        end = ticks_to_beats(raw.end_ticks, ticks_per_beat)
        if end <= start + tolerance:
            raise MidiValidationError("control: section notes must have positive duration.")
        ok, segment, nearest = quantization_details(segments, start, tolerance)
        if not ok:
            raise MidiValidationError(
                f"control: unquantized start for {midi_name(raw.midi)}. "
                f"time={normalize_number(start)} beats, "
                f"nearest={normalize_number(nearest)} beats, "
                f"timeSignature={segment.numerator}/{segment.denominator}."
            )
        ok, segment, nearest = quantization_details(segments, end, tolerance)
        if not ok:
            raise MidiValidationError(
                f"control: unquantized end for {midi_name(raw.midi)}. "
                f"time={normalize_number(end)} beats, "
                f"nearest={normalize_number(nearest)} beats, "
                f"timeSignature={segment.numerator}/{segment.denominator}."
            )
        starts.append((start, raw))
        intervals.append((start, end, raw))

    previous_interval: tuple[float, float, RawNote] | None = None
    for start, end, raw in sorted(intervals, key=lambda item: (item[0], item[1], item[2].midi)):
        if previous_interval is not None:
            previous_start, previous_end, previous_raw = previous_interval
            if start < previous_end - tolerance:
                raise MidiValidationError(
                    "control: section notes must not overlap. "
                    f"{midi_name(previous_raw.midi)}@{normalize_number(previous_start)}-"
                    f"{normalize_number(previous_end)} overlaps "
                    f"{midi_name(raw.midi)}@{normalize_number(start)}-"
                    f"{normalize_number(end)}."
                )
        if previous_interval is None or end > previous_interval[1]:
            previous_interval = (start, end, raw)

    output: list[dict[str, Any]] = []
    previous_start: float | None = None
    for index, (start, raw) in enumerate(starts):
        if previous_start is not None and abs(start - previous_start) <= tolerance:
            raise MidiValidationError("control: only one section note may start at the same time.")
        next_start = starts[index + 1][0] if index + 1 < len(starts) else duration
        if next_start <= start + tolerance:
            raise MidiValidationError("control: section notes must start before the song ends.")
        bar, beat, grid = bar_position(segments, start)
        output.append(
            {
                "section": CONTROL_SECTIONS[raw.midi % 12],
                "name": midi_name(raw.midi),
                "midi": raw.midi,
                "time": normalize_number(start),
                "duration": normalize_number(next_start - start),
                "bar": bar,
                "beat": beat,
                "grid": grid,
            }
        )
        previous_start = start
    return output


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


def validate_no_overlapping_notes(
    notes: list[dict[str, Any]],
    track_label: str,
    tolerance: float,
) -> None:
    previous: dict[str, Any] | None = None
    previous_end = 0.0

    for note in sorted(
        notes,
        key=lambda item: (float(item["time"]), float(item["duration"]), item["midi"]),
    ):
        start = float(note["time"])
        end = start + float(note["duration"])
        if previous is not None and start < previous_end - tolerance:
            raise MidiValidationError(
                f"{track_label}: overlapping notes are not allowed. "
                f"{previous['name']}@{previous['time']}+{previous['duration']} overlaps "
                f"{note['name']}@{note['time']}+{note['duration']}."
            )
        previous = note
        previous_end = max(previous_end, end)


def filter_non_chord_groups(
    notes: list[dict[str, Any]],
    tolerance: float,
) -> list[dict[str, Any]]:
    grouped_notes: dict[float, list[dict[str, Any]]] = {}
    for note in notes:
        start = float(note["time"])
        key = round(start / max(tolerance, 1e-9)) * max(tolerance, 1e-9)
        grouped_notes.setdefault(key, []).append(note)

    chord_notes: list[dict[str, Any]] = []
    for group in grouped_notes.values():
        pitch_classes = {int(note["midi"]) % 12 for note in group}
        if len(pitch_classes) > 1:
            chord_notes.extend(group)

    return sorted(
        chord_notes,
        key=lambda item: (float(item["time"]), float(item["duration"]), item["midi"]),
    )


def group_notes_by_start(
    notes: list[dict[str, Any]],
    tolerance: float,
) -> list[list[dict[str, Any]]]:
    grouped_notes: dict[float, list[dict[str, Any]]] = {}
    for note in notes:
        start = float(note["time"])
        key = round(start / max(tolerance, 1e-9)) * max(tolerance, 1e-9)
        grouped_notes.setdefault(key, []).append(note)
    return [grouped_notes[key] for key in sorted(grouped_notes)]


def note_end(note: dict[str, Any]) -> float:
    return float(note["time"]) + float(note["duration"])


def count_bars(duration: float, segments: list[TimeSignatureSegment]) -> int:
    if duration <= 0:
        return 0

    bar_count = 0
    for index, segment in enumerate(segments):
        next_start = segments[index + 1].start if index + 1 < len(segments) else duration
        segment_end = min(duration, next_start)
        if segment_end <= segment.start:
            continue
        elapsed = segment_end - segment.start
        bar_count += math.ceil((elapsed - 1e-9) / segment.bar_length)
        if duration <= next_start:
            break
    return bar_count


def duration_seconds(duration: float, tempo_map: list[dict[str, Any]]) -> float:
    seconds = 0.0
    for index, item in enumerate(tempo_map):
        start = float(item["time"])
        if start >= duration:
            break
        end = float(tempo_map[index + 1]["time"]) if index + 1 < len(tempo_map) else duration
        segment_end = min(duration, end)
        if segment_end <= start:
            continue
        seconds += (segment_end - start) * 60 / float(item["bpm"])
    return seconds


def validate_piece_limits(
    melody_notes: list[dict[str, Any]],
    chord_notes: list[dict[str, Any]],
    tempo_map: list[dict[str, Any]],
    segments: list[TimeSignatureSegment],
    duration: float,
    tolerance: float,
) -> None:
    if not melody_notes:
        raise MidiValidationError("Melody track must contain at least one valid note.")
    if not chord_notes:
        raise MidiValidationError("Chord track must contain at least one valid chord.")

    bar_count = count_bars(duration, segments)
    if bar_count > MAX_BARS:
        raise MidiValidationError(
            f"MIDI length must not exceed {MAX_BARS} bars. Found {bar_count} bars."
        )

    seconds = duration_seconds(duration, tempo_map)
    if seconds > MAX_SECONDS + tolerance:
        raise MidiValidationError(
            f"MIDI length must not exceed 8 minutes. Found {round(seconds, 2)} seconds."
        )

    for group in group_notes_by_start(chord_notes, tolerance):
        chord_start = min(float(note["time"]) for note in group)
        chord_end = max(note_end(note) for note in group)
        for melody in melody_notes:
            melody_start = float(melody["time"])
            melody_end = note_end(melody)
            if melody_start < chord_end - tolerance and melody_end > chord_start + tolerance:
                return

    raise MidiValidationError(
        "Chord track must contain at least one valid chord whose time range overlaps a melody note."
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


def output_dir_for_midi(input_path: Path, output_root: Path) -> Path:
    return output_root / input_path.stem


def output_json_paths(output_dir: Path) -> list[Path]:
    return [output_dir / name for name in OUTPUT_JSON_FILENAMES]


def existing_output_json_paths(output_dir: Path) -> list[Path]:
    return [path for path in output_json_paths(output_dir) if path.exists()]


def require_no_existing_outputs(output_dir: Path) -> None:
    existing = existing_output_json_paths(output_dir)
    if existing:
        joined = ", ".join(str(path) for path in existing)
        raise MidiValidationError(f"Output JSON file(s) already exist: {joined}")


def convert_midi_to_json(
    input_path: Path,
    output_root: Path,
    tolerance: float,
    overwrite: bool = True,
) -> Path:
    output_dir = output_dir_for_midi(input_path, output_root)
    if not overwrite:
        require_no_existing_outputs(output_dir)

    ticks_per_beat, tracks, tempo_events, time_signature_events = parse_midi(input_path)
    music_tracks = non_empty_tracks(tracks)

    validate_required_track_set(music_tracks)
    melody_track = select_named_track(music_tracks, "melody")
    chord_track = select_named_track(music_tracks, "chord")
    control_track = optional_named_track(music_tracks, "control")

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
    validate_no_overlapping_notes(melody_notes, "melody", tolerance)
    chord_notes = filter_non_chord_groups(chord_notes, tolerance)

    header = build_header(melody_notes, chord_notes, tempo_map, time_signature_map)
    validate_piece_limits(
        melody_notes,
        chord_notes,
        tempo_map,
        segments,
        float(header["duration"]),
        tolerance,
    )
    control_notes = (
        convert_control_notes(
            control_track.notes,
            ticks_per_beat,
            segments,
            float(header["duration"]),
            tolerance,
        )
        if control_track is not None
        else []
    )
    canonical = {"header": header, "melody": melody_notes, "chord": chord_notes}
    melody_payload = {"header": header, "melody": melody_notes}
    if control_notes:
        canonical["control"] = control_notes
        melody_payload["control"] = control_notes

    output_dir.mkdir(parents=True, exist_ok=True)

    write_json(output_dir / "canonical.json", canonical)
    write_json(output_dir / "melody.json", melody_payload)
    write_json(output_dir / "chord.json", {"header": header, "chord": chord_notes})

    if filtered_melody or filtered_chord:
        print(
            "Filtered out-of-range notes: "
            f"melody={filtered_melody}, chord={filtered_chord}",
            file=sys.stderr,
        )

    return output_dir


def confirm_overwrite(paths: list[Path]) -> bool:
    joined = "\n".join(str(path) for path in paths)
    try:
        answer = input(f"Output JSON file(s) already exist:\n{joined}\nOverwrite? [y/N]: ")
    except EOFError:
        return False
    return answer.strip().lower() == "y"


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

    try:
        if not input_path.exists():
            print(f"ERROR: Input MIDI file does not exist: {input_path}", file=sys.stderr)
            return 2
        if not input_path.is_file():
            print(f"ERROR: Input MIDI path is not a file: {input_path}", file=sys.stderr)
            return 2
    except OSError as exc:
        print(f"ERROR: Unable to inspect input MIDI file {input_path}: {exc}", file=sys.stderr)
        return 2

    output_dir = output_dir_for_midi(input_path, output_root)
    existing_outputs = existing_output_json_paths(output_dir)
    if existing_outputs and not confirm_overwrite(existing_outputs):
        print(f"Canceled: output JSON file(s) already exist in {output_dir}", file=sys.stderr)
        return 2

    try:
        output_dir = convert_midi_to_json(input_path, output_root, args.tolerance)
    except (MidiParseError, MidiValidationError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote JSON files to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
