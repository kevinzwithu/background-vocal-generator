#!/usr/bin/env python3
"""Expand background-vocal plans into raw data and export raw data to MIDI."""

from __future__ import annotations

import argparse
from bisect import bisect_right
import copy
import hashlib
import json
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

try:
    import project_common
except ModuleNotFoundError:
    from script import project_common


DEFAULT_TICKS_PER_BEAT = 480
MAX_JSON_FILE_BYTES = 5 * 1024 * 1024
MAX_PLAN_FILE_BYTES = 1024 * 1024
MAX_NOTE_TXT_FILE_BYTES = 100 * 1024
MAX_PAD_BLOCKS = 50_000
MAX_LEAD_OCTAVE_SHIFT = 72
V1_PLAN_SCHEMA = "bv-plan-v1"
V2_PLAN_SCHEMA = "bv-plan-v2"
PLAN_PAD_POLICIES = ("sectional", "optional", "required")
EXPECTED_PLAN_KEYS = {
    "schemaVersion", "sourceFingerprint", "padPolicy", "leadOctaveShift",
    "harmonyPitches", "padBlocks",
}
PITCH_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
VALIDATION_TOLERANCE = 0.01
MAX_BARS = project_common.MAX_BARS
MAX_SECONDS = project_common.MAX_SECONDS
MAX_NOTES_PER_TRACK = 50_000
MAX_TOTAL_NOTES = 200_000
MAX_SOURCE_MELODY_NOTES = 50_000
MAX_CONTROL_EVENTS = 50_000
MAX_LYRIC_BYTES = 4096
MAX_TOTAL_LYRIC_BYTES = 100 * 1024
MAX_INTEGER_TEXT_DIGITS = 1024
MAX_TEMPO_EVENTS = project_common.MAX_TEMPO_EVENTS
MAX_TIME_SIGNATURE_EVENTS = project_common.MAX_TIME_SIGNATURE_EVENTS
MAX_MIDI_VLQ = 0x0FFFFFFF
MAX_TICKS_PER_BEAT = 0x7FFF
MAX_TEMPO_MICROSECONDS = 0xFFFFFF
MAX_TIME_SIGNATURE_BYTE = project_common.MAX_TIME_SIGNATURE_BYTE
MAX_TOLERANCE = project_common.MAX_TOLERANCE
C0_MIDI = 12
C6_MIDI = 84
BV_HARMONY_MAX_MIDI = C6_MIDI
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

MELODY_ALIGNED_TRACKS = ("HHA", "HHB", "HHF", "LHA", "LHB", "LHF")
PAD_TRACKS = ("VocalPad1", "VocalPad2", "VocalPad3", "VocalPad4")
HIGH_HARMONY_TRACKS = ("HHA", "HHB", "HHF")
LOW_HARMONY_TRACKS = ("LHA", "LHB", "LHF")


class BvMidiError(Exception):
    pass


TimeSignatureSegment = project_common.TimeSignatureSegment


def remove_temp_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def write_bytes_atomically(path: Path, payload: bytes, overwrite: bool = True) -> None:
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(payload)
        if overwrite:
            temp_path.replace(path)
        else:
            try:
                os.link(temp_path, path)
            except FileExistsError as exc:
                raise BvMidiError(f"Output file already exists: {path}") from exc
    finally:
        if temp_path is not None:
            remove_temp_file(temp_path)


normalize_number = project_common.normalize_number


def require_number(item: dict[str, Any], key: str, label: str) -> float:
    if key not in item:
        raise BvMidiError(f"{label} is missing '{key}'.")
    try:
        value = float(item[key])
    except (TypeError, ValueError, OverflowError) as exc:
        raise BvMidiError(f"{label}.{key} must be a number.") from exc
    if not math.isfinite(value):
        raise BvMidiError(f"{label}.{key} must be a finite number.")
    return value


def require_int(item: dict[str, Any], key: str, label: str) -> int:
    if key not in item:
        raise BvMidiError(f"{label} is missing '{key}'.")
    value = item[key]
    if isinstance(value, bool):
        raise BvMidiError(f"{label}.{key} must be an integer.")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isfinite(value) and value.is_integer():
            return int(value)
        raise BvMidiError(f"{label}.{key} must be an integer.")
    if isinstance(value, str):
        stripped = value.strip()
        signless = stripped[1:] if stripped.startswith(("+", "-")) else stripped
        if signless.isdigit() and len(signless) <= MAX_INTEGER_TEXT_DIGITS:
            try:
                return int(stripped)
            except ValueError as exc:
                raise BvMidiError(f"{label}.{key} must be an integer.") from exc
    raise BvMidiError(f"{label}.{key} must be an integer.")


def require_quarter_bpm(item: dict[str, Any], key: str, label: str) -> float:
    bpm = require_number(item, key, label)
    if bpm <= 0:
        raise BvMidiError("tempoMap bpm must be positive.")
    quarter_units = bpm * 4
    if not math.isfinite(quarter_units) or abs(quarter_units - round(quarter_units)) > 1e-9:
        raise BvMidiError(f"{label}.{key} must use 0.25 BPM increments.")
    tempo_microseconds(bpm, label)
    return bpm


def tempo_microseconds(bpm: float, label: str) -> int:
    microseconds = round(60_000_000 / bpm)
    if not 1 <= microseconds <= MAX_TEMPO_MICROSECONDS:
        raise BvMidiError(f"{label}.bpm cannot be represented by a MIDI tempo event.")
    return microseconds


def validate_export_options(ticks_per_beat: int, velocity: int, tolerance: float) -> None:
    if isinstance(ticks_per_beat, bool) or not isinstance(ticks_per_beat, int):
        raise BvMidiError("ticks_per_beat must be an integer.")
    if not 1 <= ticks_per_beat <= MAX_TICKS_PER_BEAT:
        raise BvMidiError(f"ticks_per_beat must be between 1 and {MAX_TICKS_PER_BEAT}.")
    if isinstance(velocity, bool) or not isinstance(velocity, int):
        raise BvMidiError("velocity must be an integer.")
    if not 1 <= velocity <= 127:
        raise BvMidiError("velocity must be between 1 and 127.")
    if not project_common.is_valid_tolerance(tolerance):
        raise BvMidiError(f"tolerance must be between 0 and {MAX_TOLERANCE} beats.")


def reject_if_over_limit(value: int, limit: int, label: str) -> None:
    if value > limit:
        raise BvMidiError(f"{label} exceeds limit of {limit}.")


def parse_bounded_json_int(text: str) -> int:
    if len(text.lstrip("+-")) > MAX_INTEGER_TEXT_DIGITS:
        raise ValueError("integer literal is too long")
    return int(text)


def read_json(path: Path, *, max_bytes: int | None = None) -> dict[str, Any]:
    limit = MAX_JSON_FILE_BYTES if max_bytes is None else max_bytes
    try:
        if not path.exists():
            raise BvMidiError(f"Input JSON file does not exist: {path}")
        if not path.is_file():
            raise BvMidiError(f"Input JSON path is not a file: {path}")
        reject_if_over_limit(path.stat().st_size, limit, "JSON file size")
        with path.open("rb") as source:
            raw = source.read(limit + 1)
        reject_if_over_limit(len(raw), limit, "JSON file size")
    except OSError as exc:
        raise BvMidiError(f"Unable to inspect or read JSON file {path}: {exc}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"), parse_int=parse_bounded_json_int)
    except UnicodeDecodeError as exc:
        raise BvMidiError(f"JSON file must use UTF-8 encoding: {path}") from exc
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise BvMidiError(f"Invalid JSON file {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise BvMidiError(f"JSON root must be an object: {path}")
    return payload


def load_source_json(directory: Path, *, required: bool = False) -> dict[str, Any]:
    """Read one source snapshot, preferring melody.json over canonical.json."""
    for name in ("melody.json", "canonical.json"):
        path = directory / name
        if not path.exists():
            continue
        source = read_json(path)
        for key, limit, label in (
            ("melody", MAX_SOURCE_MELODY_NOTES, "Source melody note count"),
            ("control", MAX_CONTROL_EVENTS, "Control event count"),
        ):
            if isinstance(source.get(key), list):
                reject_if_over_limit(len(source[key]), limit, label)
        return source
    if required:
        raise BvMidiError(f"Missing required source melody.json or canonical.json in {directory}")
    return {}


def validate_source_requirement(
    tracks: dict[str, list[dict[str, Any]]],
    melody: list[dict[str, Any]] | None,
    allow_standalone: bool,
) -> None:
    if melody is not None:
        return
    if not allow_standalone:
        raise BvMidiError(
            "Missing required source melody.json or canonical.json beside bv_raw.json. "
            "Use --allow-standalone only for legacy projects without lyrics."
        )
    if any("lyric" in note for notes in tracks.values() for note in notes):
        raise BvMidiError(
            "Standalone export does not allow lyric fields because source melody is unavailable."
        )


def resolve_header(payload: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    header = dict(source["header"]) if isinstance(source.get("header"), dict) else {}

    raw_header = payload.get("header")
    if isinstance(raw_header, dict):
        header.update(raw_header)
    elif "header" in payload:
        raise BvMidiError("header must be an object when provided.")

    header.setdefault("startTime", 0)
    header.setdefault("duration", 0)
    header.setdefault("timeUnit", "beats")
    header.setdefault("grid", project_common.GRID_LABEL)
    header.setdefault("tempoMap", [{"time": 0, "bpm": 120}])
    header.setdefault("timeSignatureMap", [{"time": 0, "numerator": 4, "denominator": 4}])

    if header["timeUnit"] != "beats":
        raise BvMidiError("header.timeUnit must be 'beats'.")
    if header["grid"] != project_common.GRID_LABEL:
        raise BvMidiError(f"header.grid must be '{project_common.GRID_LABEL}'.")

    start_time = require_number(header, "startTime", "header")
    duration = require_number(header, "duration", "header")
    if abs(start_time) > 1e-9:
        raise BvMidiError("header.startTime must be 0.")
    if duration < 0:
        raise BvMidiError("header.duration cannot be negative.")

    validate_tempo_map(header["tempoMap"])
    validate_time_signature_map(header["timeSignatureMap"])
    return header


def validate_tempo_map(tempo_map: Any) -> None:
    if not isinstance(tempo_map, list) or not tempo_map:
        raise BvMidiError("header.tempoMap must be a non-empty list.")
    reject_if_over_limit(len(tempo_map), MAX_TEMPO_EVENTS, "Tempo event count")
    previous_time = -1.0
    for index, item in enumerate(tempo_map):
        if not isinstance(item, dict):
            raise BvMidiError("Each tempoMap item must be an object.")
        label = f"tempoMap[{index}]"
        time = require_number(item, "time", label)
        require_quarter_bpm(item, "bpm", label)
        if time < 0:
            raise BvMidiError("tempoMap time cannot be negative.")
        if time <= previous_time:
            raise BvMidiError("tempoMap times must be strictly increasing.")
        previous_time = time
    if abs(require_number(tempo_map[0], "time", "tempoMap[0]")) > 1e-9:
        raise BvMidiError("tempoMap must start at time 0.")


def validate_time_signature_map(time_signature_map: Any) -> None:
    if not isinstance(time_signature_map, list) or not time_signature_map:
        raise BvMidiError("header.timeSignatureMap must be a non-empty list.")
    reject_if_over_limit(
        len(time_signature_map),
        MAX_TIME_SIGNATURE_EVENTS,
        "Time signature event count",
    )
    previous_time = -1.0
    for index, item in enumerate(time_signature_map):
        if not isinstance(item, dict):
            raise BvMidiError("Each timeSignatureMap item must be an object.")
        label = f"timeSignatureMap[{index}]"
        time = require_number(item, "time", label)
        numerator = require_int(item, "numerator", label)
        denominator = require_int(item, "denominator", label)
        if time < 0:
            raise BvMidiError("timeSignatureMap time cannot be negative.")
        if time <= previous_time:
            raise BvMidiError("timeSignatureMap times must be strictly increasing.")
        if numerator <= 0:
            raise BvMidiError("timeSignatureMap numerator must be positive.")
        if numerator > MAX_TIME_SIGNATURE_BYTE:
            raise BvMidiError(
                f"timeSignatureMap numerator must be at most {MAX_TIME_SIGNATURE_BYTE}."
            )
        if denominator <= 0 or denominator & (denominator - 1):
            raise BvMidiError("timeSignatureMap denominator must be a power of two.")
        if denominator.bit_length() - 1 > MAX_TIME_SIGNATURE_BYTE:
            raise BvMidiError(
                f"timeSignatureMap denominator exponent must be at most {MAX_TIME_SIGNATURE_BYTE}."
            )
        previous_time = time
    if abs(require_number(time_signature_map[0], "time", "timeSignatureMap[0]")) > 1e-9:
        raise BvMidiError("timeSignatureMap must start at time 0.")


def build_time_signature_segments(header: dict[str, Any]) -> list[TimeSignatureSegment]:
    try:
        return project_common.build_time_signature_segments(header["timeSignatureMap"])
    except ValueError as exc:
        raise BvMidiError(str(exc)) from exc


def quantized(
    segments: list[TimeSignatureSegment],
    time: float,
    tolerance: float,
) -> tuple[bool, float, TimeSignatureSegment]:
    segment = project_common.segment_for_time(segments, time)
    offset = time - segment.start
    ok, nearest_offset = project_common.snap_to_music_grid(
        offset,
        segment.bar_length,
        segment.numerator,
        tolerance,
    )
    return ok, segment.start + nearest_offset, segment


def validate_piece_limits(header: dict[str, Any], segments: list[TimeSignatureSegment]) -> None:
    duration = float(header["duration"])
    bar_count, seconds = project_common.piece_metrics(
        duration,
        segments,
        header["tempoMap"],
    )
    if bar_count > MAX_BARS:
        raise BvMidiError(
            f"Arrangement length must not exceed {MAX_BARS} bars. Found {bar_count} bars."
        )
    if seconds > MAX_SECONDS:
        raise BvMidiError(
            f"Arrangement length must not exceed 10 minutes. Found {round(seconds, 2)} seconds."
        )


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

    total_notes = 0
    for track_name in EXPECTED_TRACKS:
        notes = tracks[track_name]
        if not isinstance(notes, list):
            raise BvMidiError(f"Track {track_name} must be a list.")
        reject_if_over_limit(len(notes), MAX_NOTES_PER_TRACK, f"Track {track_name} note count")
        total_notes += len(notes)
    reject_if_over_limit(total_notes, MAX_TOTAL_NOTES, "Total BV note count")
    return {name: tracks[name] for name in EXPECTED_TRACKS}


def validate_notes(
    tracks: dict[str, list[dict[str, Any]]],
    segments: list[TimeSignatureSegment],
    tolerance: float,
) -> float:
    max_end = 0.0
    total_lyric_bytes = 0
    for track_name, notes in tracks.items():
        track_notes: list[tuple[float, float, int, dict[str, Any]]] = []
        for note in notes:
            if not isinstance(note, dict):
                raise BvMidiError(f"Track {track_name} contains a non-object note.")

            label = f"Track {track_name} note"
            if "name" not in note:
                raise BvMidiError(f"{label} is missing 'name'.")
            midi = require_int(note, "midi", label)
            start = require_number(note, "time", label)
            duration = require_number(note, "duration", label)
            end = start + duration
            if "lyric" in note:
                if track_name in PAD_TRACKS:
                    raise BvMidiError(f"Track {track_name} note must not contain lyric.")
                lyric = note["lyric"]
                if not isinstance(lyric, str):
                    raise BvMidiError(f"Track {track_name} note lyric must be a string.")
                if "\x00" in lyric:
                    raise BvMidiError(f"Track {track_name} note lyric must not contain NUL.")
                lyric_bytes = len(lyric.encode("utf-8"))
                reject_if_over_limit(lyric_bytes, MAX_LYRIC_BYTES, "Lyric size")
                total_lyric_bytes += lyric_bytes

            max_midi = C6_MIDI if track_name == "LeadDouble" else BV_HARMONY_MAX_MIDI
            if midi < C0_MIDI or midi > max_midi:
                raise BvMidiError(
                    f"Track {track_name} note {note.get('name')} midi={midi} is outside "
                    f"the allowed range for that track."
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

            track_notes.append((start, end, midi, note))
            max_end = max(max_end, end)

        previous_end = -1.0
        previous_note: dict[str, Any] | None = None
        for start, end, _, note in sorted(track_notes, key=lambda item: (item[0], item[1], item[2])):
            if start < previous_end - tolerance:
                raise BvMidiError(
                    f"Track {track_name} has overlapping notes: "
                    f"{previous_note} overlaps {note}."
                )
            previous_end = max(previous_end, end)
            previous_note = note
    reject_if_over_limit(total_lyric_bytes, MAX_TOTAL_LYRIC_BYTES, "Total lyric size")
    return max_end


def validate_melody_aligned_harmonies(
    tracks: dict[str, list[dict[str, Any]]],
    melody: list[dict[str, Any]] | None,
    tolerance: float,
    control: list[dict[str, Any]] | None = None,
    *, sparse: bool = False,
) -> None:
    if melody is None:
        return

    spans = no_bv_spans(control, tolerance)

    for track_name in PAD_TRACKS:
        span_index = 0
        for note in sorted(tracks[track_name], key=lambda item: float(item["time"])):
            note_start = float(note["time"])
            note_end = note_start + float(note["duration"])
            while (
                span_index < len(spans)
                and spans[span_index][1] <= note_start + tolerance
            ):
                span_index += 1
            if span_index >= len(spans):
                break
            span_start, span_end = spans[span_index]
            if note_start < span_end - tolerance and note_end > span_start + tolerance:
                raise BvMidiError(
                    f"Track {track_name} note at time={note_start:g} duration="
                    f"{float(note['duration']):g} overlaps no_bv span "
                    f"{span_start:g}..{span_end:g}."
                )

    def requires_harmony(note: dict[str, Any]) -> bool:
        start = float(note["time"])
        low = 0
        high = len(spans)
        while low < high:
            middle = (low + high) // 2
            if spans[middle][0] - tolerance <= start:
                low = middle + 1
            else:
                high = middle
        if low == 0:
            return True
        span_start, span_end = spans[low - 1]
        return not (span_start - tolerance <= start < span_end - tolerance)

    for index, melody_note in enumerate(melody):
        if not isinstance(melody_note, dict):
            raise BvMidiError(f"Source melody note at index {index} must be an object.")
        label = f"Source melody note at index {index}"
        if "name" not in melody_note:
            raise BvMidiError(f"{label} is missing 'name'.")
        require_int(melody_note, "midi", label)
        require_number(melody_note, "time", label)
        require_number(melody_note, "duration", label)
        if "lyric" in melody_note and not isinstance(melody_note["lyric"], str):
            raise BvMidiError(f"{label}.lyric must be a string.")

    lead_double = tracks["LeadDouble"]
    if len(lead_double) != len(melody):
        raise BvMidiError(
            "LeadDouble must contain exactly one note per melody note. "
            f"Expected {len(melody)}, found {len(lead_double)}."
        )

    harmony_melody = [note for note in melody if requires_harmony(note)]
    for track_name in MELODY_ALIGNED_TRACKS:
        if not sparse and len(tracks[track_name]) != len(harmony_melody):
            raise BvMidiError(
                f"{track_name} must contain exactly one note per harmony-enabled melody note. "
                f"Expected {len(harmony_melody)}, found {len(tracks[track_name])}."
            )

    octave_shift: int | None = None
    for index, melody_note in enumerate(melody):
        lead_note = lead_double[index]
        if (
            abs(float(lead_note["time"]) - float(melody_note["time"])) > tolerance
            or abs(float(lead_note["duration"]) - float(melody_note["duration"])) > tolerance
        ):
            raise BvMidiError(
                "LeadDouble must preserve melody timing. "
                f"Mismatch at index {index}."
            )
        shift = int(lead_note["midi"]) - int(melody_note["midi"])
        if shift % 12 != 0:
            raise BvMidiError(
                "LeadDouble pitch changes must be a uniform whole-octave transposition. "
                f"Found non-octave shift {shift} semitones at index {index}."
            )
        if octave_shift is None:
            octave_shift = shift
        elif shift != octave_shift:
            raise BvMidiError(
                "LeadDouble pitch changes must use one uniform octave shift for the entire melody. "
                f"Expected {octave_shift} semitones, found {shift} at index {index}."
            )
        expected_lyric = melody_note.get("lyric")
        if lead_note.get("lyric") != expected_lyric:
            raise BvMidiError(
                f"Track LeadDouble note at time={melody_note['time']} must inherit "
                f"lyric {expected_lyric!r}; found {lead_note.get('lyric')!r}."
            )

    harmony_lead = [
        lead_note
        for source_note, lead_note in zip(melody, lead_double)
        if requires_harmony(source_note)
    ]
    harmony_by_time = {float(note["time"]): note for note in harmony_lead}
    for track_name in MELODY_ALIGNED_TRACKS:
        matched_times: set[float] = set()
        for index, harmony_note in enumerate(tracks[track_name]):
            if sparse:
                onset = float(harmony_note["time"])
                if onset not in harmony_by_time or onset in matched_times:
                    raise BvMidiError(f"{track_name} has no unique source onset at {onset:g}.")
                melody_note = harmony_by_time[onset]
                matched_times.add(onset)
            else:
                melody_note = harmony_lead[index]
            melody_midi = int(melody_note["midi"])
            expected_duration = float(melody_note["duration"])
            if sparse:
                for left, right in spans:
                    if float(melody_note["time"]) < left < float(melody_note["time"]) + expected_duration:
                        expected_duration = left - float(melody_note["time"])
                        break
            if (
                abs(float(harmony_note["time"]) - float(melody_note["time"])) > tolerance
                or abs(float(harmony_note["duration"]) - expected_duration) > tolerance
            ):
                raise BvMidiError(
                    f"{track_name} must align to melody timing at index {index}. "
                    f"Melody time={melody_note['time']} duration={melody_note['duration']}; "
                    f"{track_name} time={harmony_note['time']} duration={harmony_note['duration']}."
                )

            harmony_midi = int(harmony_note["midi"])
            if harmony_midi == melody_midi:
                raise BvMidiError(
                    f"{track_name} must use a different pitch from melody at index {index}."
                )
            if track_name in HIGH_HARMONY_TRACKS and harmony_midi <= melody_midi:
                raise BvMidiError(
                    f"{track_name} must be higher than melody at index {index}. "
                    f"melody={melody_midi}, harmony={harmony_midi}."
                )
            if track_name in LOW_HARMONY_TRACKS and harmony_midi >= melody_midi:
                raise BvMidiError(
                    f"{track_name} must be lower than melody at index {index}. "
                    f"melody={melody_midi}, harmony={harmony_midi}."
                )
            expected_lyric = melody_note.get("lyric")
            if harmony_note.get("lyric") != expected_lyric:
                raise BvMidiError(
                    f"Track {track_name} note at time={melody_note['time']} must inherit "
                    f"lyric {expected_lyric!r}; found {harmony_note.get('lyric')!r}."
                )


def vlq(value: int) -> bytes:
    if value < 0:
        raise BvMidiError(f"Cannot write negative delta time: {value}")
    if value > MAX_MIDI_VLQ:
        raise BvMidiError(f"MIDI variable-length value exceeds limit of {MAX_MIDI_VLQ}: {value}")
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

    for tick, _, event_payload in sorted(events, key=lambda item: (item[0], item[1])):
        payload.extend(vlq(tick - previous_tick))
        payload.extend(event_payload)
        previous_tick = tick

    payload.extend(vlq(end_tick - previous_tick))
    payload.extend(meta_event(0x2F, b""))
    return b"MTrk" + len(payload).to_bytes(4, "big") + bytes(payload)


def beats_to_ticks(beats: float, ticks_per_beat: int) -> int:
    ticks = beats * ticks_per_beat
    if not math.isfinite(ticks):
        raise BvMidiError("MIDI tick position must be finite.")
    rounded = round(ticks)
    if rounded > MAX_MIDI_VLQ:
        raise BvMidiError(f"MIDI tick position exceeds limit of {MAX_MIDI_VLQ}: {rounded}")
    return rounded


def conductor_track(header: dict[str, Any], ticks_per_beat: int, end_tick: int) -> bytes:
    events: list[tuple[int, int, bytes]] = [(0, 0, meta_event(0x03, b"conductor"))]

    for item in header["tempoMap"]:
        tick = beats_to_ticks(float(item["time"]), ticks_per_beat)
        bpm = float(item["bpm"])
        microseconds_per_quarter = tempo_microseconds(bpm, "tempoMap item")
        events.append((tick, 1, meta_event(0x51, microseconds_per_quarter.to_bytes(3, "big"))))

    for item in header["timeSignatureMap"]:
        tick = beats_to_ticks(float(item["time"]), ticks_per_beat)
        numerator = int(item["numerator"])
        denominator = int(item["denominator"])
        denominator_power = denominator.bit_length() - 1
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
        if "lyric" in note:
            events.append((start_tick, 1, meta_event(0x05, note["lyric"].encode("utf-8"))))
        events.append((end_note_tick, 2, bytes([0x80, midi, 0])))
        events.append((start_tick, 3, bytes([0x90, midi, velocity])))

    return track_chunk(events, end_tick)


def write_midi(
    path: Path,
    header: dict[str, Any],
    tracks: dict[str, list[dict[str, Any]]],
    ticks_per_beat: int,
    velocity: int,
    max_note_end: float,
    overwrite: bool = True,
) -> None:
    if path.exists() and not overwrite:
        raise BvMidiError(f"Output MIDI file already exists: {path}")

    header_duration = validate_header_bounds(header, max_note_end)
    end_tick = beats_to_ticks(header_duration, ticks_per_beat)

    chunks = [conductor_track(header, ticks_per_beat, end_tick)]
    for track_name in EXPECTED_TRACKS:
        chunks.append(
            note_track(track_name, tracks[track_name], ticks_per_beat, end_tick, velocity)
        )

    midi_header_payload = (
        (1).to_bytes(2, "big")
        + len(chunks).to_bytes(2, "big")
        + ticks_per_beat.to_bytes(2, "big")
    )
    midi_header = b"MThd" + len(midi_header_payload).to_bytes(4, "big") + midi_header_payload

    path.parent.mkdir(parents=True, exist_ok=True)
    write_bytes_atomically(path, midi_header + b"".join(chunks), overwrite)


def validate_header_bounds(header: dict[str, Any], max_note_end: float) -> float:
    header_duration = float(header.get("duration", 0) or 0)
    if max_note_end > header_duration + 1e-9:
        raise BvMidiError(
            f"header.duration={normalize_number(header_duration)} is shorter than "
            f"the latest BV note end={normalize_number(max_note_end)}."
        )
    latest_header_event = max(
        max(float(item["time"]) for item in header["tempoMap"]),
        max(float(item["time"]) for item in header["timeSignatureMap"]),
    )
    if latest_header_event > header_duration + 1e-9:
        raise BvMidiError(
            f"header.duration={normalize_number(header_duration)} is shorter than "
            f"the latest tempo or time signature event={normalize_number(latest_header_event)}."
        )
    return header_duration


def midi_name(midi: int) -> str:
    return f"{PITCH_NAMES[midi % 12]}{midi // 12 - 1}"


def read_note_text(path: Path) -> str:
    try:
        if not path.exists():
            raise BvMidiError(f"Missing required note.txt: {path}")
        if not path.is_file():
            raise BvMidiError(f"note.txt path is not a file: {path}")
        reject_if_over_limit(path.stat().st_size, MAX_NOTE_TXT_FILE_BYTES, "note.txt size")
        with path.open("rb") as source:
            raw = source.read(MAX_NOTE_TXT_FILE_BYTES + 1)
        reject_if_over_limit(len(raw), MAX_NOTE_TXT_FILE_BYTES, "note.txt size")
    except OSError as exc:
        raise BvMidiError(f"Unable to inspect or read note.txt {path}: {exc}") from exc
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BvMidiError("note.txt must use UTF-8.") from exc


def validate_header(header: Any) -> dict[str, Any]:
    if not isinstance(header, dict):
        raise BvMidiError("Source header must be an object.")
    required = {
        "startTime",
        "duration",
        "timeUnit",
        "grid",
        "tempoMap",
        "timeSignatureMap",
    }
    missing = sorted(required - set(header))
    if missing:
        raise BvMidiError(f"Source header is missing: {', '.join(missing)}")
    if header["timeUnit"] != "beats":
        raise BvMidiError("Source header.timeUnit must be 'beats'.")
    if header["grid"] != "1/32_bar":
        raise BvMidiError("Source header.grid must be '1/32_bar'.")
    start = require_number(header, "startTime", "Source header")
    duration = require_number(header, "duration", "Source header")
    validate_tempo_map(header["tempoMap"])
    validate_time_signature_map(header["timeSignatureMap"])
    segments = build_time_signature_segments(header)
    validate_piece_limits(header, segments)
    if abs(start) > 1e-9:
        raise BvMidiError("Source header.startTime must be 0.")
    if duration <= 0:
        raise BvMidiError("Source header.duration must be positive.")
    return copy.deepcopy(header)


def validate_source_melody(melody: Any, duration: float) -> list[dict[str, Any]]:
    if not isinstance(melody, list):
        raise BvMidiError("Source JSON must contain a melody list.")
    reject_if_over_limit(
        len(melody),
        MAX_SOURCE_MELODY_NOTES,
        "Source melody note count",
    )
    validated: list[dict[str, Any]] = []
    for index, note in enumerate(melody):
        if not isinstance(note, dict):
            raise BvMidiError(f"Source melody note at index {index} must be an object.")
        label = f"Source melody note at index {index}"
        if "name" not in note or not isinstance(note["name"], str):
            raise BvMidiError(f"{label}.name must be a string.")
        midi = require_int(note, "midi", label)
        time = require_number(note, "time", label)
        note_duration = require_number(note, "duration", label)
        if time < 0 or note_duration <= 0 or time + note_duration > duration + 1e-9:
            raise BvMidiError(f"{label} is outside source header.duration.")
        if not 0 <= midi <= 127:
            raise BvMidiError(f"{label}.midi must be between 0 and 127.")
        if "lyric" in note:
            lyric = note["lyric"]
            if not isinstance(lyric, str):
                raise BvMidiError(f"{label}.lyric must be a string.")
            if "\x00" in lyric:
                raise BvMidiError(f"{label}.lyric must not contain NUL.")
            reject_if_over_limit(
                len(lyric.encode("utf-8")),
                MAX_LYRIC_BYTES,
                f"{label} lyric size",
            )
        validated.append(note)
    return validated


def validate_control(control: Any, duration: float) -> list[dict[str, Any]]:
    if control is None:
        return []
    if not isinstance(control, list):
        raise BvMidiError("Source control must be a list when present.")
    reject_if_over_limit(
        len(control), MAX_CONTROL_EVENTS, "Source control event count"
    )
    for index, item in enumerate(control):
        if not isinstance(item, dict):
            raise BvMidiError(f"Source control item at index {index} must be an object.")
        if item.get("section") != "no_bv":
            continue
        label = f"Source control item at index {index}"
        start = require_number(item, "time", label)
        span_duration = require_number(item, "duration", label)
        if start < 0 or span_duration <= 0 or start + span_duration > duration + 1e-9:
            raise BvMidiError(f"{label} no_bv span is outside source duration.")
    return control


def load_source_bundle(
    directory: Path, source: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], str]:
    if source is None:
        source = load_source_json(directory, required=True)
    header = validate_header(source.get("header"))
    melody = validate_source_melody(source.get("melody"), float(header["duration"]))
    control = validate_control(source.get("control", []), float(header["duration"]))
    note_text = read_note_text(directory / "note.txt")
    return header, melody, control, note_text


def source_fingerprint(
    header: dict[str, Any],
    melody: list[dict[str, Any]],
    control: list[dict[str, Any]],
    note_text: str,
) -> str:
    canonical = json.dumps(
        {
            "header": header,
            "melody": melody,
            "control": control,
            "noteTxt": note_text,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def no_bv_spans(
    control: list[dict[str, Any]] | None,
    tolerance: float = VALIDATION_TOLERANCE,
) -> list[tuple[float, float]]:
    raw_no_bv_spans: list[tuple[float, float]] = []
    for index, item in enumerate(control or []):
        if not isinstance(item, dict):
            raise BvMidiError(f"Source control item at index {index} must be an object.")
        if item.get("section") != "no_bv":
            continue
        label = f"Source control item at index {index}"
        start = require_number(item, "time", label)
        duration = require_number(item, "duration", label)
        if start < 0 or duration <= 0:
            raise BvMidiError(f"{label} must have non-negative time and positive duration.")
        raw_no_bv_spans.append((start, start + duration))

    merged: list[tuple[float, float]] = []
    for start, end in sorted(raw_no_bv_spans):
        if merged and start <= merged[-1][1] + tolerance:
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))

    return merged

def harmony_enabled_melody(
    melody: list[dict[str, Any]], control: list[dict[str, Any]] | None,
    tolerance: float = VALIDATION_TOLERANCE,
) -> list[dict[str, Any]]:
    spans = no_bv_spans(control, tolerance)
    starts = [start for start, _ in spans]

    def enabled(note: dict[str, Any]) -> bool:
        start = float(note["time"])
        index = bisect_right(starts, start + tolerance) - 1
        if index < 0:
            return True
        span_start, span_end = spans[index]
        return not (
            span_start - tolerance
            <= start
            < span_end - tolerance
        )

    return [note for note in melody if enabled(note)]


def require_json_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BvMidiError(f"{label} must be a JSON integer.")
    return value


def require_finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BvMidiError(f"{label} must be a JSON number.")
    resolved = float(value)
    if not math.isfinite(resolved):
        raise BvMidiError(f"{label} must be finite.")
    return resolved


def validate_plan(
    plan: dict[str, Any],
    expected_fingerprint: str,
    harmony_note_count: int,
    header: dict[str, Any],
) -> dict[str, Any]:
    missing = sorted(EXPECTED_PLAN_KEYS - set(plan))
    extra = sorted(set(plan) - EXPECTED_PLAN_KEYS)
    if missing:
        raise BvMidiError(f"Plan is missing key(s): {', '.join(missing)}")
    if extra:
        raise BvMidiError(f"Plan has unexpected key(s): {', '.join(extra)}")
    if plan["schemaVersion"] not in (V1_PLAN_SCHEMA, V2_PLAN_SCHEMA):
        raise BvMidiError("schemaVersion must be bv-plan-v1 or bv-plan-v2.")
    sparse = plan["schemaVersion"] == V2_PLAN_SCHEMA
    fingerprint = plan["sourceFingerprint"]
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise BvMidiError("sourceFingerprint must be a 64-character SHA-256 hex string.")
    try:
        int(fingerprint, 16)
    except ValueError as exc:
        raise BvMidiError("sourceFingerprint must contain lowercase hexadecimal characters.") from exc
    if fingerprint != fingerprint.lower():
        raise BvMidiError("sourceFingerprint must use lowercase hexadecimal characters.")
    if fingerprint != expected_fingerprint:
        raise BvMidiError(
            "sourceFingerprint does not match melody/header/control/note.txt. "
            f"Expected {expected_fingerprint}, found {fingerprint}."
        )
    if plan["padPolicy"] not in (("free",) if sparse else PLAN_PAD_POLICIES):
        raise BvMidiError(
            "padPolicy must be free for v2, or sectional/optional/required for v1."
        )
    lead_shift = require_json_int(plan["leadOctaveShift"], "leadOctaveShift")
    if lead_shift % 12 != 0 or abs(lead_shift) > MAX_LEAD_OCTAVE_SHIFT:
        raise BvMidiError(
            f"leadOctaveShift must be a whole-octave value between -{MAX_LEAD_OCTAVE_SHIFT} and {MAX_LEAD_OCTAVE_SHIFT}."
        )

    harmony_pitches = plan["harmonyPitches"]
    if not isinstance(harmony_pitches, dict):
        raise BvMidiError("harmonyPitches must be an object.")
    missing_tracks = sorted(set(MELODY_ALIGNED_TRACKS) - set(harmony_pitches))
    extra_tracks = sorted(set(harmony_pitches) - set(MELODY_ALIGNED_TRACKS))
    if missing_tracks or extra_tracks:
        raise BvMidiError(
            "harmonyPitches must contain exactly "
            f"{', '.join(MELODY_ALIGNED_TRACKS)}; missing={missing_tracks}, extra={extra_tracks}."
        )
    for track_name in MELODY_ALIGNED_TRACKS:
        pitches = harmony_pitches[track_name]
        if not isinstance(pitches, list):
            raise BvMidiError(f"harmonyPitches.{track_name} must be a list.")
        reject_if_over_limit(
            len(pitches),
            MAX_SOURCE_MELODY_NOTES,
            f"harmonyPitches.{track_name} decision count",
        )
        if len(pitches) != harmony_note_count:
            raise BvMidiError(
                f"harmonyPitches.{track_name} must contain exactly {harmony_note_count} "
                f"harmony-enabled melody decisions; found {len(pitches)}."
            )
        for index, pitch in enumerate(pitches):
            if sparse and pitch is None:
                continue
            midi = require_json_int(pitch, f"harmonyPitches.{track_name}[{index}]")
            if not C0_MIDI <= midi <= C6_MIDI:
                raise BvMidiError(
                    f"harmonyPitches.{track_name}[{index}] must be MIDI 12..84."
                )

    pad_blocks = plan["padBlocks"]
    if not isinstance(pad_blocks, list):
        raise BvMidiError("padBlocks must be a list.")
    reject_if_over_limit(len(pad_blocks), MAX_PAD_BLOCKS, "Pad block count")
    previous_time = -1.0
    previous_end = -1.0
    source_duration = float(header["duration"])
    for index, block in enumerate(pad_blocks):
        if not isinstance(block, dict):
            raise BvMidiError(f"padBlocks[{index}] must be an object.")
        if set(block) != {"time", "duration", "pitches"}:
            raise BvMidiError(
                f"padBlocks[{index}] must contain exactly time, duration, pitches."
            )
        time = require_finite_number(block["time"], f"padBlocks[{index}].time")
        duration = require_finite_number(
            block["duration"], f"padBlocks[{index}].duration"
        )
        pitches = block["pitches"]
        if not isinstance(pitches, list) or len(pitches) != len(PAD_TRACKS):
            raise BvMidiError(f"padBlocks[{index}].pitches must contain exactly four MIDI values.")
        resolved_pitches = [
            require_json_int(pitch, f"padBlocks[{index}].pitches[{pitch_index}]")
            for pitch_index, pitch in enumerate(pitches)
        ]
        if any(
            midi < C0_MIDI or midi > C6_MIDI
            for midi in resolved_pitches
        ):
            raise BvMidiError(f"padBlocks[{index}].pitches must stay within MIDI 12..84.")
        if not all(
            upper > lower
            for upper, lower in zip(resolved_pitches, resolved_pitches[1:])
        ):
            raise BvMidiError(
                f"padBlocks[{index}].pitches must descend in VocalPad1..4 order without crossing."
            )
        if time < 0 or duration <= 0 or time + duration > source_duration + 1e-9:
            raise BvMidiError(f"padBlocks[{index}] is outside source header.duration.")
        if time < previous_time - 1e-9:
            raise BvMidiError("padBlocks must be ordered by non-decreasing time.")
        if time < previous_end - VALIDATION_TOLERANCE:
            raise BvMidiError(f"padBlocks[{index}] overlaps the previous Pad block.")
        previous_time = time
        previous_end = time + duration
    return plan


def note_from_source(source: dict[str, Any], midi: int) -> dict[str, Any]:
    note: dict[str, Any] = {
        "name": midi_name(midi),
        "midi": midi,
        "time": source["time"],
        "duration": source["duration"],
    }
    if "lyric" in source:
        note["lyric"] = source["lyric"]
    return note


def expand_plan(
    plan: dict[str, Any],
    header: dict[str, Any],
    melody: list[dict[str, Any]],
    control: list[dict[str, Any]],
) -> dict[str, Any]:
    tracks: dict[str, list[dict[str, Any]]] = {
        track_name: [] for track_name in EXPECTED_TRACKS
    }
    lead_shift = int(plan["leadOctaveShift"])
    for source_note in melody:
        tracks["LeadDouble"].append(
            note_from_source(source_note, int(source_note["midi"]) + lead_shift)
        )

    sparse = plan["schemaVersion"] == V2_PLAN_SCHEMA
    harmony_melody = melody if sparse else harmony_enabled_melody(melody, control)
    spans = no_bv_spans(control)
    for track_name in MELODY_ALIGNED_TRACKS:
        tracks[track_name] = [
            note_from_source(source_note, int(midi))
            for source_note, midi in zip(
                harmony_melody, plan["harmonyPitches"][track_name]
            )
            if midi is not None
        ]

    if sparse:
        for track_name in MELODY_ALIGNED_TRACKS:
            for note in tracks[track_name]:
                clip_at_silence(note, spans)

    for block in plan["padBlocks"]:
        time = normalize_number(float(block["time"]))
        duration = normalize_number(float(block["duration"]))
        for track_name, midi in zip(PAD_TRACKS, block["pitches"]):
            tracks[track_name].append(
                {
                    "name": midi_name(int(midi)),
                    "midi": int(midi),
                    "time": time,
                    "duration": duration,
                }
            )
    if sparse:
        for track_name in PAD_TRACKS:
            for note in tracks[track_name]:
                clip_at_silence(note, spans)
    return {"header": copy.deepcopy(header), "tracks": tracks}


def clip_at_silence(note: dict[str, Any], spans: list[tuple[float, float]]) -> None:
    """Cut a sustain at the first silence boundary; never resume it."""
    start = float(note["time"])
    end = start + float(note["duration"])
    for left, right in spans:
        if left - VALIDATION_TOLERANCE <= start < right - VALIDATION_TOLERANCE:
            raise BvMidiError(f"A sounding decision starts inside no_bv at {start:g}; use null/rest.")
        if start < left < end:
            note["duration"] = normalize_number(left - start)
            break


def validate_rendered_payload(
    payload: dict[str, Any],
    melody: list[dict[str, Any]],
    control: list[dict[str, Any]],
    *, sparse: bool = False,
) -> None:
    header = payload["header"]
    tracks = payload["tracks"]
    try:
        segments = build_time_signature_segments(header)
        max_note_end = validate_notes(
            tracks, segments, VALIDATION_TOLERANCE
        )
        validate_header_bounds(header, max_note_end)
        validate_melody_aligned_harmonies(
            tracks, melody, VALIDATION_TOLERANCE, control, sparse=sparse
        )
    except BvMidiError as exc:
        raise BvMidiError(f"Rendered plan violates raw contract: {exc}") from exc


def deterministic_raw_bytes(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")


def resolve_plan_path(path: Path) -> Path:
    return path / "bv_plan.json" if path.is_dir() else path


def prepare_plan_render(
    plan_path: Path, *, source: dict[str, Any] | None = None,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    """Validate a plan and expand it in memory without writing bv_raw.json."""
    resolved_plan_path = resolve_plan_path(plan_path)
    directory = resolved_plan_path.parent
    header, melody, control, note_text = load_source_bundle(directory, source)
    fingerprint = source_fingerprint(header, melody, control, note_text)
    plan = read_json(resolved_plan_path, max_bytes=MAX_PLAN_FILE_BYTES)
    harmony_melody = harmony_enabled_melody(melody, control)
    sparse = plan.get("schemaVersion") == V2_PLAN_SCHEMA
    validate_plan(plan, fingerprint, len(melody) if sparse else len(harmony_melody), header)
    payload = expand_plan(plan, header, melody, control)
    validate_rendered_payload(payload, melody, control, sparse=sparse)
    return resolved_plan_path, plan, payload


def render_plan(
    plan_path: Path,
    output_path: Path | None = None,
    *,
    force: bool = False,
) -> Path:
    resolved_plan_path, _, payload = prepare_plan_render(plan_path)
    directory = resolved_plan_path.parent
    destination = output_path or directory / "bv_raw.json"
    protected_paths = {
        resolved_plan_path.resolve(),
        (directory / "melody.json").resolve(),
        (directory / "canonical.json").resolve(),
        (directory / "note.txt").resolve(),
    }
    if destination.resolve() in protected_paths:
        raise BvMidiError(
            f"Output path must not replace a plan or source file: {destination}"
        )
    write_bytes_atomically(destination, deterministic_raw_bytes(payload), overwrite=force)
    return destination


def fingerprint_for_path(path: Path) -> str:
    resolved = resolve_plan_path(path)
    header, melody, control, note_text = load_source_bundle(resolved.parent)
    return source_fingerprint(header, melody, control, note_text)


def convert_bv_raw_to_midi(
    bv_path: Path,
    output_path: Path | None = None,
    ticks_per_beat: int = DEFAULT_TICKS_PER_BEAT,
    velocity: int = 80,
    tolerance: float = 0.01,
    overwrite: bool = True,
    allow_standalone: bool = False,
    *, source: dict[str, Any] | None = None,
) -> Path:
    validate_export_options(ticks_per_beat, velocity, tolerance)
    payload = read_json(bv_path)
    if source is None:
        source = load_source_json(bv_path.parent)
    header = resolve_header(payload, source)
    tracks = load_tracks(payload)
    segments = build_time_signature_segments(header)
    validate_piece_limits(header, segments)
    max_note_end = validate_notes(tracks, segments, tolerance)
    melody = source.get("melody") if isinstance(source.get("melody"), list) else None
    control = source.get("control") if isinstance(source.get("control"), list) else []
    validate_source_requirement(tracks, melody, allow_standalone)
    sparse = False
    plan_path = bv_path.parent / "bv_plan.json"
    if plan_path.exists():
        try:
            _, plan, expected = prepare_plan_render(plan_path, source=source)
            if payload != expected:
                raise BvMidiError("Raw does not match sibling plan; rerender before export.")
            sparse = plan["schemaVersion"] == "bv-plan-v2"
        except (ValueError, BvMidiError) as exc:
            raise BvMidiError(f"Unable to verify sibling plan: {exc}") from exc
    validate_melody_aligned_harmonies(
        tracks,
        melody,
        tolerance,
        control,
        sparse=sparse,
    )

    destination = output_path or (bv_path.parent / "bv.mid")
    write_midi(destination, header, tracks, ticks_per_beat, velocity, max_note_end, overwrite)
    return destination


def confirm_overwrite(path: Path) -> bool:
    try:
        answer = input(f"Output MIDI file already exists: {path}\nOverwrite? [y/N]: ")
    except EOFError:
        return False
    return answer.strip().lower() == "y"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a BV plan or export raw BV data to MIDI.")
    parser.add_argument("path", help="Raw JSON for MIDI export; work directory or plan JSON for plan operations.")
    parser.add_argument("-o", "--output", help="Output path. Default: sibling bv.mid, or bv_raw.json with --render-plan.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--render-plan", action="store_true", help="Expand a plan into bv_raw.json.")
    mode.add_argument(
        "--print-source-fingerprint", action="store_true",
        help="Print the source fingerprint before authoring a plan.",
    )
    parser.add_argument("--force", action="store_true", help="Replace existing raw data with --render-plan.")
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
    parser.add_argument(
        "--allow-standalone",
        action="store_true",
        help="Allow legacy export without source melody; lyric fields remain forbidden.",
    )
    args = parser.parse_args(argv)
    if args.force and not args.render_plan:
        parser.error("--force requires --render-plan.")
    if args.print_source_fingerprint and args.output:
        parser.error("--print-source-fingerprint cannot be combined with --output.")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    bv_path = Path(args.path).expanduser()
    output_path = Path(args.output).expanduser() if args.output else None
    if args.print_source_fingerprint or args.render_plan:
        try:
            if args.print_source_fingerprint:
                print(fingerprint_for_path(bv_path))
            else:
                destination = render_plan(bv_path, output_path, force=args.force)
                print(f"Wrote raw BV to {destination}")
            return 0
        except (BvMidiError, OSError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    destination = output_path or (bv_path.parent / "bv.mid")

    try:
        if not bv_path.exists():
            print(f"ERROR: bv_raw.json does not exist: {bv_path}", file=sys.stderr)
            return 2
        if not bv_path.is_file():
            print(f"ERROR: bv_raw.json path is not a file: {bv_path}", file=sys.stderr)
            return 2
    except OSError as exc:
        print(f"ERROR: Unable to inspect bv_raw.json {bv_path}: {exc}", file=sys.stderr)
        return 2
    try:
        validate_export_options(args.ticks_per_beat, args.velocity, args.tolerance)
    except BvMidiError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    try:
        destination_exists = destination.exists()
    except OSError as exc:
        print(f"ERROR: Unable to inspect output MIDI file {destination}: {exc}", file=sys.stderr)
        return 2
    if destination_exists and not confirm_overwrite(destination):
        print(f"Canceled: output MIDI file already exists: {destination}", file=sys.stderr)
        return 2

    try:
        source = load_source_json(bv_path.parent)
        destination = convert_bv_raw_to_midi(
            bv_path,
            output_path,
            args.ticks_per_beat,
            args.velocity,
            args.tolerance,
            overwrite=destination_exists,
            allow_standalone=args.allow_standalone,
            source=source,
        )
    except (BvMidiError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.allow_standalone and not isinstance(source.get("melody"), list):
        print(
            "WARNING: Exported standalone MIDI without source melody alignment or lyrics.",
            file=sys.stderr,
        )
    print(f"Wrote MIDI to {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
