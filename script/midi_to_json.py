#!/usr/bin/env python3
"""Convert MIDI melody/chord/control tracks into project canonical JSON.

The parser intentionally uses only the Python standard library so the first
project phase can run without installing dependencies.
"""

from __future__ import annotations

import argparse
import codecs
import json
import math
import os
import sys
import tempfile
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import project_common
except ModuleNotFoundError:
    from script import project_common


C0_MIDI = 12
C6_MIDI = 84
HARMONY_HEADROOM_SEMITONES = 7
PREFERRED_MELODY_MIN_MIDI = C0_MIDI + HARMONY_HEADROOM_SEMITONES
PREFERRED_MELODY_MAX_MIDI = C6_MIDI - HARMONY_HEADROOM_SEMITONES
MAX_BARS = project_common.MAX_BARS
MAX_SECONDS = project_common.MAX_SECONDS
MAX_MIDI_FILE_BYTES = 100 * 1024
MAX_TRACKS = 64
MAX_MIDI_EVENTS = 200_000
MAX_PARSED_NOTES = 50_000
MAX_TEMPO_EVENTS = project_common.MAX_TEMPO_EVENTS
MAX_TIME_SIGNATURE_EVENTS = project_common.MAX_TIME_SIGNATURE_EVENTS
MAX_TOLERANCE = project_common.MAX_TOLERANCE
MAX_LYRIC_BYTES = 4096
MAX_TOTAL_LYRIC_BYTES = 100 * 1024
DEFAULT_LYRIC_ENCODING = "utf-8"
ALLOWED_LYRIC_ENCODINGS = ("utf-8", "gb18030", "big5", "latin-1")
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
    midi: int
    start_ticks: int
    end_ticks: int


@dataclass(frozen=True)
class RawLyric:
    ticks: int
    text: str
    order: int


@dataclass(frozen=True)
class TrackData:
    name: str | None
    notes: list[RawNote]
    lyrics: list[RawLyric]


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
    try:
        return raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        return raw.decode("latin-1").strip()


def normalize_lyric_encoding(encoding: str) -> str:
    try:
        normalized = codecs.lookup(encoding).name
    except LookupError as exc:
        raise MidiValidationError(f"Unsupported lyric encoding: {encoding}.") from exc
    aliases = {
        "utf-8": "utf-8",
        "gb18030": "gb18030",
        "big5": "big5",
        "iso8859-1": "latin-1",
    }
    selected = aliases.get(normalized)
    if selected is None:
        raise MidiValidationError(
            "lyric_encoding must be one of: " + ", ".join(ALLOWED_LYRIC_ENCODINGS) + "."
        )
    return selected


def decode_lyric(raw: bytes, encoding: str, ticks: int) -> str:
    try:
        text = raw.decode(encoding)
    except UnicodeDecodeError as exc:
        hint = (
            " Specify --lyric-encoding gb18030, big5, or latin-1 if the MIDI "
            "uses a legacy encoding."
            if encoding == DEFAULT_LYRIC_ENCODING
            else ""
        )
        raise MidiParseError(
            f"Lyric event at tick {ticks} is not valid {encoding}.{hint}"
        ) from exc
    if "\x00" in text:
        raise MidiParseError("Lyric meta event must not contain NUL characters.")
    return text


def midi_name(midi: int) -> str:
    octave = midi // 12 - 1
    return f"{PITCH_NAMES[midi % 12]}{octave}"


normalize_number = project_common.normalize_number


def normalize_melody_register(
    notes: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Create upper/lower harmony headroom using one uniform octave shift."""
    if not notes:
        return notes, 0

    minimum = min(int(note["midi"]) for note in notes)
    maximum = max(int(note["midi"]) for note in notes)
    candidate_shifts = sorted(range(-72, 73, 12), key=lambda shift: (abs(shift), shift))
    for shift in candidate_shifts:
        if (
            minimum + shift >= PREFERRED_MELODY_MIN_MIDI
            and maximum + shift <= PREFERRED_MELODY_MAX_MIDI
        ):
            if shift == 0:
                return notes, 0
            shifted_notes = []
            for note in notes:
                shifted_midi = int(note["midi"]) + shift
                shifted_notes.append(
                    {**note, "name": midi_name(shifted_midi), "midi": shifted_midi}
                )
            return shifted_notes, shift

    raise MidiValidationError(
        "melody: no uniform whole-octave shift can place the melody within "
        f"{midi_name(PREFERRED_MELODY_MIN_MIDI)}..{midi_name(PREFERRED_MELODY_MAX_MIDI)} "
        "while reserving upper and lower harmony headroom. "
        f"Filtered melody range is {midi_name(minimum)}..{midi_name(maximum)}."
    )


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


def validate_tolerance(tolerance: float) -> None:
    if not project_common.is_valid_tolerance(tolerance):
        raise MidiValidationError(
            f"tolerance must be between 0 and {MAX_TOLERANCE} beats."
        )


def parse_track(
    data: bytes,
    ticks_per_beat: int,
    track_index: int,
    event_order_start: int,
    lyric_encoding: str = DEFAULT_LYRIC_ENCODING,
) -> tuple[TrackData, list[TempoEvent], list[TimeSignatureEvent], int]:
    del ticks_per_beat

    offset = 0
    abs_ticks = 0
    running_status: int | None = None
    track_name: str | None = None
    notes: list[RawNote] = []
    lyrics: list[RawLyric] = []
    tempo_events: list[TempoEvent] = []
    time_signature_events: list[TimeSignatureEvent] = []
    active_notes: dict[tuple[int, int], deque[int]] = {}
    event_order = event_order_start
    saw_end_of_track = False

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

            if meta_type == 0x2F:
                if payload:
                    raise MidiParseError(f"Track {track_index}: End-of-Track payload must be empty.")
                if offset != len(data):
                    raise MidiParseError(f"Track {track_index}: data found after End-of-Track event.")
                saw_end_of_track = True
                event_order += 1
                break
            if meta_type == 0x03:
                track_name = decode_text(payload)
            elif meta_type == 0x05:
                reject_if_over_limit(length, MAX_LYRIC_BYTES, "lyric event size")
                text = decode_lyric(payload, lyric_encoding, abs_ticks)
                if text:
                    lyrics.append(RawLyric(abs_ticks, text, event_order))
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
                if numerator <= 0:
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
                active_notes.setdefault(key, deque()).append(abs_ticks)
            else:
                starts = active_notes.get(key)
                if starts:
                    start_ticks = starts.popleft()
                    if not starts:
                        del active_notes[key]
                    notes.append(RawNote(note, start_ticks, abs_ticks))
                    reject_if_over_limit(len(notes), MAX_PARSED_NOTES, "parsed note count")
        event_order += 1

    if not saw_end_of_track:
        raise MidiParseError(f"Track {track_index}: missing End-of-Track event.")

    if active_notes:
        pending = []
        for (_, note), starts in active_notes.items():
            for start in starts:
                pending.append(f"{midi_name(note)} at tick {start}")
        joined = ", ".join(pending[:5])
        raise MidiParseError(f"Track {track_index}: unclosed note(s): {joined}.")

    return TrackData(track_name, notes, lyrics), tempo_events, time_signature_events, event_order


def parse_midi(
    path: Path,
    lyric_encoding: str = DEFAULT_LYRIC_ENCODING,
) -> tuple[int, list[TrackData], list[TempoEvent], list[TimeSignatureEvent]]:
    try:
        file_size = path.stat().st_size
    except OSError as exc:
        raise MidiParseError(f"Unable to inspect MIDI file: {exc}") from exc
    reject_if_over_limit(file_size, MAX_MIDI_FILE_BYTES, "file size")

    try:
        with path.open("rb") as source:
            data = source.read(MAX_MIDI_FILE_BYTES + 1)
    except OSError as exc:
        raise MidiParseError(f"Unable to read MIDI file: {exc}") from exc
    reject_if_over_limit(len(data), MAX_MIDI_FILE_BYTES, "file size")
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
    if midi_format not in (0, 1):
        raise MidiParseError(f"Unsupported MIDI format: {midi_format}.")
    if track_count <= 0:
        raise MidiParseError("MIDI must declare at least one track.")
    if midi_format == 0 and track_count != 1:
        raise MidiParseError("MIDI format 0 must declare exactly one track.")

    tracks: list[TrackData] = []
    tempo_events: list[TempoEvent] = []
    time_signature_events: list[TimeSignatureEvent] = []
    event_order = 0
    parsed_note_count = 0
    lyric_byte_count = 0

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
            lyric_encoding,
        )
        tracks.append(track)
        parsed_note_count += len(track.notes)
        lyric_byte_count += sum(len(item.text.encode("utf-8")) for item in track.lyrics)
        tempo_events.extend(track_tempos)
        time_signature_events.extend(track_sigs)
        reject_if_over_limit(parsed_note_count, MAX_PARSED_NOTES, "parsed note count")
        reject_if_over_limit(lyric_byte_count, MAX_TOTAL_LYRIC_BYTES, "total lyric size")
        reject_if_over_limit(len(tempo_events), MAX_TEMPO_EVENTS, "tempo event count")
        reject_if_over_limit(
            len(time_signature_events),
            MAX_TIME_SIGNATURE_EVENTS,
            "time signature event count",
        )

    if offset != len(data):
        raise MidiParseError(
            f"MIDI contains {len(data) - offset} trailing byte(s) after declared tracks."
        )

    return ticks_per_beat, tracks, tempo_events, time_signature_events


def ticks_to_beats(ticks: int, ticks_per_beat: int) -> float:
    return ticks / ticks_per_beat


def normalize_tempo_map(events: list[TempoEvent], ticks_per_beat: int) -> list[dict[str, Any]]:
    by_time: dict[float, int | float] = {}
    for event in sorted(events, key=lambda item: (item.ticks, item.order)):
        by_time[ticks_to_beats(event.ticks, ticks_per_beat)] = event.bpm

    if 0.0 not in by_time:
        by_time[0.0] = 120

    timeline = [
        {"time": normalize_number(time), "bpm": bpm}
        for time, bpm in sorted(by_time.items())
    ]
    return remove_redundant_timeline_values(timeline, ("bpm",))


def normalize_time_signature_map(
    events: list[TimeSignatureEvent],
    ticks_per_beat: int,
) -> list[dict[str, Any]]:
    by_time: dict[float, tuple[int, int]] = {}
    for event in sorted(events, key=lambda item: (item.ticks, item.order)):
        by_time[ticks_to_beats(event.ticks, ticks_per_beat)] = (event.numerator, event.denominator)

    if 0.0 not in by_time:
        by_time[0.0] = (4, 4)

    timeline = [
        {
            "time": normalize_number(time),
            "numerator": numerator,
            "denominator": denominator,
        }
        for time, (numerator, denominator) in sorted(by_time.items())
    ]
    return remove_redundant_timeline_values(timeline, ("numerator", "denominator"))


def remove_redundant_timeline_values(
    timeline: list[dict[str, Any]],
    value_keys: tuple[str, ...],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    previous: tuple[Any, ...] | None = None
    for item in timeline:
        value = tuple(item[key] for key in value_keys)
        if value != previous:
            output.append(item)
            previous = value
    return output


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
        grid_size = project_common.straight_grid_size_beats(bar_length)

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


def quantization_details(
    segments: list[TimeSignatureSegment],
    time: float,
    tolerance: float,
) -> tuple[bool, TimeSignatureSegment, float]:
    segment = project_common.segment_for_time(segments, time)
    offset = time - segment.start
    ok, nearest_offset = project_common.snap_to_music_grid(
        offset,
        segment.bar_length,
        segment.numerator,
        tolerance,
    )
    return ok, segment, segment.start + nearest_offset


def bar_position(segments: list[TimeSignatureSegment], time: float) -> tuple[int, int | float, int]:
    segment = project_common.segment_for_time(segments, time)
    offset = max(0.0, time - segment.start)
    bar_offset = math.floor((offset + 1e-9) / segment.bar_length)
    bar_start = segment.start + bar_offset * segment.bar_length
    offset_in_bar = max(0.0, time - bar_start)
    beat = 1 + offset_in_bar
    grid = round(offset_in_bar / segment.grid_size)
    if grid >= project_common.STRAIGHT_GRID_DIVISIONS_PER_BAR:
        grid = project_common.STRAIGHT_GRID_DIVISIONS_PER_BAR - 1
    return segment.start_bar + bar_offset, normalize_number(beat), grid


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


def bind_melody_lyrics(track: TrackData, source_path: Path) -> dict[int, str]:
    note_starts = {note.start_ticks for note in track.notes}
    lyrics_by_start: dict[int, list[RawLyric]] = {}
    for lyric in sorted(track.lyrics, key=lambda item: (item.ticks, item.order)):
        if lyric.ticks not in note_starts:
            raise MidiValidationError(
                f"Lyric event at tick {lyric.ticks} in {source_path} "
                "does not match a melody note start."
            )
        lyrics_by_start.setdefault(lyric.ticks, []).append(lyric)
    return {
        ticks: "".join(item.text for item in items)
        for ticks, items in lyrics_by_start.items()
    }


def convert_notes(
    raw_notes: list[RawNote],
    ticks_per_beat: int,
    segments: list[TimeSignatureSegment],
    tolerance: float,
    track_label: str,
    lyrics_by_start: dict[int, str] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    output: list[dict[str, Any]] = []
    filtered = 0

    for raw in sorted(raw_notes, key=lambda item: (item.start_ticks, item.end_ticks, item.midi)):
        if raw.end_ticks <= raw.start_ticks:
            raise MidiValidationError(
                f"{track_label}: note {midi_name(raw.midi)} midi={raw.midi} has non-positive duration."
            )

        if raw.midi < C0_MIDI or raw.midi > C6_MIDI:
            if lyrics_by_start and raw.start_ticks in lyrics_by_start:
                raise MidiValidationError(
                    f"{track_label}: note {midi_name(raw.midi)} midi={raw.midi} at "
                    f"tick {raw.start_ticks} has lyric {lyrics_by_start[raw.start_ticks]!r} "
                    "but would be filtered as out of range."
                )
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
        note = {
                "name": midi_name(raw.midi),
                "midi": raw.midi,
                "time": normalize_number(start),
                "duration": normalize_number(duration),
                "bar": bar,
                "beat": beat,
                "grid": grid,
            }
        if lyrics_by_start and raw.start_ticks in lyrics_by_start:
            note["lyric"] = lyrics_by_start[raw.start_ticks]
        output.append(note)

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
    for group in group_notes_by_start(notes, tolerance):
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
    chord_notes: list[dict[str, Any]] = []
    for group in group_notes_by_start(notes, tolerance):
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
    grouping_tolerance = max(tolerance, 1e-9)
    for note in notes:
        start = float(note["time"])
        key = round(start / grouping_tolerance) * grouping_tolerance
        grouped_notes.setdefault(key, []).append(note)
    return [grouped_notes[key] for key in sorted(grouped_notes)]


def note_end(note: dict[str, Any]) -> float:
    return float(note["time"]) + float(note["duration"])


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

    bar_count, seconds = project_common.piece_metrics(duration, segments, tempo_map)
    if bar_count > MAX_BARS:
        raise MidiValidationError(
            f"MIDI length must not exceed {MAX_BARS} bars. Found {bar_count} bars."
        )

    if seconds > MAX_SECONDS:
        raise MidiValidationError(
            f"MIDI length must not exceed 10 minutes. Found {round(seconds, 2)} seconds."
        )

    melody_intervals = sorted(
        (float(note["time"]), note_end(note)) for note in melody_notes
    )
    chord_intervals = sorted(
        (
            min(float(note["time"]) for note in group),
            max(note_end(note) for note in group),
        )
        for group in group_notes_by_start(chord_notes, tolerance)
    )
    melody_index = 0
    for chord_start, chord_end in chord_intervals:
        while (
            melody_index < len(melody_intervals)
            and melody_intervals[melody_index][1] <= chord_start + tolerance
        ):
            melody_index += 1
        if melody_index >= len(melody_intervals):
            break
        melody_start, melody_end = melody_intervals[melody_index]
        if melody_start < chord_end - tolerance and melody_end > chord_start + tolerance:
            return

    raise MidiValidationError(
        "Chord track must contain at least one valid chord whose time range overlaps a melody note."
    )


def validate_header_events_within_duration(
    tempo_map: list[dict[str, Any]],
    time_signature_map: list[dict[str, Any]],
    duration: float,
) -> None:
    latest_event = max(
        max(float(item["time"]) for item in tempo_map),
        max(float(item["time"]) for item in time_signature_map),
    )
    if latest_event > duration + 1e-9:
        raise MidiValidationError(
            f"Header event at {normalize_number(latest_event)} beats is after "
            f"the final note end at {normalize_number(duration)} beats."
        )


def build_header(
    melody_notes: list[dict[str, Any]],
    chord_notes: list[dict[str, Any]],
    tempo_map: list[dict[str, Any]],
    time_signature_map: list[dict[str, Any]],
) -> dict[str, Any]:
    duration = max((note_end(note) for note in melody_notes + chord_notes), default=0.0)

    return {
        "startTime": 0,
        "duration": normalize_number(duration),
        "timeUnit": "beats",
        "grid": project_common.GRID_LABEL,
        "tempoMap": tempo_map,
        "timeSignatureMap": time_signature_map,
    }


def remove_temp_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def prepare_temp_text(path: Path, text: str) -> Path:
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(text)
        return temp_path
    except Exception:
        if temp_path is not None:
            remove_temp_file(temp_path)
        raise


def write_json_files(
    outputs: list[tuple[Path, dict[str, Any]]],
    overwrite: bool = True,
) -> None:
    serialized = [
        (path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        for path, payload in outputs
    ]
    prepared: list[tuple[Path, Path]] = []
    linked: list[tuple[Path, int, int]] = []
    try:
        for path, text in serialized:
            prepared.append((prepare_temp_text(path, text), path))
        for temp_path, path in prepared:
            if overwrite:
                temp_path.replace(path)
            else:
                try:
                    os.link(temp_path, path)
                except FileExistsError as exc:
                    raise MidiValidationError(f"Output JSON file already exists: {path}") from exc
                linked_stat = temp_path.stat()
                linked.append((path, linked_stat.st_dev, linked_stat.st_ino))
    except Exception:
        if not overwrite:
            for path, device, inode in linked:
                try:
                    current = path.lstat()
                    if current.st_dev == device and current.st_ino == inode:
                        remove_temp_file(path)
                except OSError:
                    pass
        raise
    finally:
        for temp_path, _ in prepared:
            remove_temp_file(temp_path)


def validate_work_name(work_name: str) -> None:
    if (
        not work_name
        or Path(work_name).name != work_name
        or "\\" in work_name
    ):
        raise MidiValidationError(
            "work_name must be one safe directory name without path separators."
        )


def parse_midi_with_path(
    path: Path,
    lyric_encoding: str,
) -> tuple[int, list[TrackData], list[TempoEvent], list[TimeSignatureEvent]]:
    try:
        return parse_midi(path, lyric_encoding=lyric_encoding)
    except MidiParseError as exc:
        raise MidiParseError(f"{path}: {exc}") from exc


def select_role_track(
    path: Path,
    tracks: list[TrackData],
    role: str,
) -> tuple[int, TrackData]:
    non_empty_tracks = [
        (index, track)
        for index, track in enumerate(tracks)
        if track.notes
    ]
    if not non_empty_tracks:
        raise MidiValidationError(
            f"{path}: no non-empty tracks found for role '{role}'."
        )
    if len(non_empty_tracks) == 1:
        selected_index, selected = non_empty_tracks[0]
    else:
        matches = [
            (index, track)
            for index, track in non_empty_tracks
            if track.name is not None and track.name.strip().lower() == role
        ]
        if not matches:
            raise MidiValidationError(
                f"{path}: multiple non-empty tracks found, but none is named '{role}'."
            )
        if len(matches) > 1:
            raise MidiValidationError(
                f"{path}: multiple non-empty tracks are named '{role}'."
            )
        selected_index, selected = matches[0]
    return selected_index, selected


def output_json_paths(output_dir: Path) -> list[Path]:
    return [output_dir / name for name in OUTPUT_JSON_FILENAMES]


def existing_output_json_paths(output_dir: Path) -> list[Path]:
    return [path for path in output_json_paths(output_dir) if path.exists()]


def require_no_existing_outputs(output_dir: Path) -> None:
    existing = existing_output_json_paths(output_dir)
    if existing:
        joined = ", ".join(str(path) for path in existing)
        raise MidiValidationError(f"Output JSON file(s) already exist: {joined}")


def convert_selected_tracks_to_json(
    melody_track: TrackData,
    melody_ticks_per_beat: int,
    melody_path: Path,
    chord_track: TrackData,
    chord_ticks_per_beat: int,
    control_track: TrackData | None,
    control_ticks_per_beat: int | None,
    tempo_events: list[TempoEvent],
    time_signature_events: list[TimeSignatureEvent],
    output_root: Path,
    work_name: str,
    tolerance: float = 0.01,
    overwrite: bool = True,
) -> Path:
    validate_tolerance(tolerance)
    validate_work_name(work_name)
    output_dir = output_root / work_name
    if not overwrite:
        require_no_existing_outputs(output_dir)

    tempo_map = normalize_tempo_map(tempo_events, melody_ticks_per_beat)
    time_signature_map = normalize_time_signature_map(
        time_signature_events, melody_ticks_per_beat
    )
    segments = build_time_signature_segments(time_signature_map)

    lyrics_by_start = bind_melody_lyrics(melody_track, melody_path)
    melody_notes, filtered_melody = convert_notes(
        melody_track.notes,
        melody_ticks_per_beat,
        segments,
        tolerance,
        "melody",
        lyrics_by_start,
    )
    chord_notes, filtered_chord = convert_notes(
        chord_track.notes,
        chord_ticks_per_beat,
        segments,
        tolerance,
        "chord",
    )
    melody_notes, melody_octave_shift = normalize_melody_register(melody_notes)
    validate_chord_alignment(chord_notes, tolerance)
    validate_no_overlapping_notes(melody_notes, "melody", tolerance)
    chord_notes = filter_non_chord_groups(chord_notes, tolerance)

    header = build_header(melody_notes, chord_notes, tempo_map, time_signature_map)
    duration = float(header["duration"])
    validate_header_events_within_duration(
        tempo_map,
        time_signature_map,
        duration,
    )
    validate_piece_limits(
        melody_notes,
        chord_notes,
        tempo_map,
        segments,
        duration,
        tolerance,
    )
    control_notes = (
        convert_control_notes(
            control_track.notes,
            int(control_ticks_per_beat),
            segments,
            duration,
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

    write_json_files(
        [
            (output_dir / "canonical.json", canonical),
            (output_dir / "melody.json", melody_payload),
            (output_dir / "chord.json", {"header": header, "chord": chord_notes}),
        ],
        overwrite=overwrite,
    )

    if filtered_melody or filtered_chord:
        print(
            "Filtered out-of-range notes: "
            f"melody={filtered_melody}, chord={filtered_chord}",
            file=sys.stderr,
        )
    if melody_octave_shift:
        print(
            "Normalized melody register for harmony headroom: "
            f"{melody_octave_shift:+d} semitones.",
            file=sys.stderr,
        )

    return output_dir


def convert_midi_to_json(
    input_path: Path,
    output_root: Path,
    tolerance: float,
    overwrite: bool = True,
    lyric_encoding: str = DEFAULT_LYRIC_ENCODING,
    work_name: str | None = None,
) -> Path:
    validate_tolerance(tolerance)
    lyric_encoding = normalize_lyric_encoding(lyric_encoding)
    output_work_name = work_name or input_path.stem
    validate_work_name(output_work_name)
    ticks_per_beat, tracks, tempos, time_signatures = parse_midi_with_path(
        input_path, lyric_encoding
    )
    music_tracks = [track for track in tracks if track.notes]
    validate_required_track_set(music_tracks)
    tracks_by_name = {
        track.name.strip().lower(): track
        for track in music_tracks
        if track.name is not None
    }
    return convert_selected_tracks_to_json(
        tracks_by_name["melody"],
        ticks_per_beat,
        input_path,
        tracks_by_name["chord"],
        ticks_per_beat,
        tracks_by_name.get("control"),
        ticks_per_beat if "control" in tracks_by_name else None,
        tempos,
        time_signatures,
        output_root,
        output_work_name,
        tolerance,
        overwrite,
    )


def convert_role_midis_to_json(
    melody_path: Path,
    chord_path: Path,
    control_path: Path | None,
    output_root: Path,
    work_name: str | None = None,
    tolerance: float = 0.01,
    overwrite: bool = True,
    lyric_encoding: str = DEFAULT_LYRIC_ENCODING,
) -> Path:
    validate_tolerance(tolerance)
    lyric_encoding = normalize_lyric_encoding(lyric_encoding)
    output_work_name = work_name or melody_path.stem
    validate_work_name(output_work_name)
    paths = [melody_path, chord_path] + ([control_path] if control_path else [])
    parsed_by_path: dict[
        Path,
        tuple[int, list[TrackData], list[TempoEvent], list[TimeSignatureEvent]],
    ] = {}
    for path in paths:
        resolved = path.expanduser().resolve()
        if resolved not in parsed_by_path:
            parsed_by_path[resolved] = parse_midi_with_path(path, lyric_encoding)

    selected: dict[str, tuple[Path, int, TrackData, int]] = {}
    for role, path in (
        ("melody", melody_path),
        ("chord", chord_path),
        ("control", control_path),
    ):
        if path is None:
            continue
        resolved = path.expanduser().resolve()
        ticks_per_beat, tracks, _, _ = parsed_by_path[resolved]
        track_index, track = select_role_track(path, tracks, role)
        selected[role] = (resolved, track_index, track, ticks_per_beat)

    identities = [(item[0], item[1]) for item in selected.values()]
    if len(set(identities)) != len(identities):
        raise MidiValidationError(
            "The same MIDI track cannot be selected for more than one role."
        )

    melody_resolved, _, melody_track, melody_tpb = selected["melody"]
    _, _, chord_track, chord_tpb = selected["chord"]
    control_selected = selected.get("control")
    _, _, tempos, time_signatures = parsed_by_path[melody_resolved]
    return convert_selected_tracks_to_json(
        melody_track,
        melody_tpb,
        melody_path,
        chord_track,
        chord_tpb,
        control_selected[2] if control_selected else None,
        control_selected[3] if control_selected else None,
        tempos,
        time_signatures,
        output_root,
        output_work_name,
        tolerance,
        overwrite,
    )


def confirm_overwrite(paths: list[Path]) -> bool:
    joined = "\n".join(str(path) for path in paths)
    try:
        answer = input(f"Output JSON file(s) already exist:\n{joined}\nOverwrite? [y/N]: ")
    except EOFError:
        return False
    return answer.strip().lower() == "y"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert MIDI melody/chord/control tracks to canonical JSON."
    )
    parser.add_argument(
        "input_midi",
        nargs="?",
        help="Single MIDI file containing named melody/chord and optional control tracks.",
    )
    parser.add_argument("--melody", help="MIDI file providing the melody track.")
    parser.add_argument("--chord", help="MIDI file providing the chord track.")
    parser.add_argument("--control", help="Optional MIDI file providing the control track.")
    parser.add_argument("--work-name", help="Safe output project directory name.")
    parser.add_argument(
        "--lyric-encoding",
        default=DEFAULT_LYRIC_ENCODING,
        choices=ALLOWED_LYRIC_ENCODINGS,
        help="Encoding used by MIDI lyric events. Default: utf-8",
    )
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
    args = parse_args(argv if argv is not None else sys.argv[1:])
    output_root = Path(args.output_root).expanduser()
    role_mode = any((args.melody, args.chord, args.control))
    if role_mode and args.input_midi:
        print(
            "ERROR: Positional input_midi cannot be used with --melody, --chord, or --control.",
            file=sys.stderr,
        )
        return 2
    if role_mode and (not args.melody or not args.chord):
        print(
            "ERROR: Role input mode requires both --melody and --chord; --control is optional.",
            file=sys.stderr,
        )
        return 2
    if not role_mode and not args.input_midi:
        print(
            "ERROR: Provide input_midi, or use both --melody and --chord.",
            file=sys.stderr,
        )
        return 2

    input_paths = (
        [Path(args.melody).expanduser(), Path(args.chord).expanduser()]
        + ([Path(args.control).expanduser()] if args.control else [])
        if role_mode
        else [Path(args.input_midi).expanduser()]
    )
    for input_path in dict.fromkeys(input_paths):
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

    default_name_path = Path(args.melody).expanduser() if role_mode else input_paths[0]
    output_work_name = args.work_name or default_name_path.stem
    try:
        validate_work_name(output_work_name)
        validate_tolerance(args.tolerance)
    except MidiValidationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    output_dir = output_root / output_work_name
    try:
        existing_outputs = existing_output_json_paths(output_dir)
    except OSError as exc:
        print(f"ERROR: Unable to inspect output JSON files in {output_dir}: {exc}", file=sys.stderr)
        return 2
    if existing_outputs and not confirm_overwrite(existing_outputs):
        print(f"Canceled: output JSON file(s) already exist in {output_dir}", file=sys.stderr)
        return 2

    try:
        if not role_mode:
            output_dir = convert_midi_to_json(
                input_paths[0],
                output_root,
                args.tolerance,
                overwrite=bool(existing_outputs),
                lyric_encoding=args.lyric_encoding,
                work_name=args.work_name,
            )
        else:
            output_dir = convert_role_midis_to_json(
                Path(args.melody).expanduser(),
                Path(args.chord).expanduser(),
                Path(args.control).expanduser() if args.control else None,
                output_root,
                args.work_name,
                args.tolerance,
                overwrite=bool(existing_outputs),
                lyric_encoding=args.lyric_encoding,
            )
    except (MidiParseError, MidiValidationError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote JSON files to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
