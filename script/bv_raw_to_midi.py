#!/usr/bin/env python3
"""Convert project bv_raw.json into a Standard MIDI file."""

from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_TICKS_PER_BEAT = 480
MAX_JSON_FILE_BYTES = 5 * 1024 * 1024
MAX_NOTES_PER_TRACK = 50_000
MAX_TOTAL_NOTES = 200_000
MAX_SOURCE_MELODY_NOTES = 50_000
MAX_CONTROL_EVENTS = 50_000
MAX_TEMPO_EVENTS = 2_000
MAX_TIME_SIGNATURE_EVENTS = 1_000
MAX_MIDI_VLQ = 0x0FFFFFFF
MAX_TICKS_PER_BEAT = 0x7FFF
MAX_TEMPO_MICROSECONDS = 0xFFFFFF
MAX_TIME_SIGNATURE_BYTE = 0xFF
C0_MIDI = 12
C6_MIDI = 84
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
HIGH_HARMONY_TRACKS = ("HHA", "HHB", "HHF")
LOW_HARMONY_TRACKS = ("LHA", "LHB", "LHF")


class BvMidiError(Exception):
    pass


@dataclass(frozen=True)
class TimeSignatureSegment:
    start: float
    numerator: int
    denominator: int
    bar_length: float
    grid_size: float


def remove_temp_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def write_bytes_atomically(path: Path, payload: bytes) -> None:
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
        temp_path.replace(path)
    finally:
        if temp_path is not None:
            remove_temp_file(temp_path)


def normalize_number(value: float, digits: int = 6) -> int | float:
    if abs(value - round(value)) < 1e-9:
        return int(round(value))
    return round(value, digits)


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
        if signless.isdigit():
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
    if not isinstance(tolerance, (int, float)) or isinstance(tolerance, bool):
        raise BvMidiError("tolerance must be a finite non-negative number.")
    if not math.isfinite(float(tolerance)) or tolerance < 0:
        raise BvMidiError("tolerance must be a finite non-negative number.")


def reject_if_over_limit(value: int, limit: int, label: str) -> None:
    if value > limit:
        raise BvMidiError(f"{label} exceeds limit of {limit}.")


def read_json(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            raise BvMidiError(f"Input JSON file does not exist: {path}")
        if not path.is_file():
            raise BvMidiError(f"Input JSON path is not a file: {path}")
        reject_if_over_limit(path.stat().st_size, MAX_JSON_FILE_BYTES, "JSON file size")
        with path.open("rb") as source:
            raw = source.read(MAX_JSON_FILE_BYTES + 1)
        reject_if_over_limit(len(raw), MAX_JSON_FILE_BYTES, "JSON file size")
    except OSError as exc:
        raise BvMidiError(f"Unable to inspect or read JSON file {path}: {exc}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise BvMidiError(f"JSON file must use UTF-8 encoding: {path}") from exc
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
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


def sibling_melody(bv_path: Path) -> list[dict[str, Any]] | None:
    for name in ("melody.json", "canonical.json"):
        candidate = bv_path.parent / name
        if candidate.exists():
            payload = read_json(candidate)
            melody = payload.get("melody")
            if isinstance(melody, list):
                reject_if_over_limit(
                    len(melody),
                    MAX_SOURCE_MELODY_NOTES,
                    "Source melody note count",
                )
                return melody
    return None


def sibling_control(bv_path: Path) -> list[dict[str, Any]]:
    for name in ("melody.json", "canonical.json"):
        candidate = bv_path.parent / name
        if candidate.exists():
            payload = read_json(candidate)
            control = payload.get("control")
            if isinstance(control, list):
                reject_if_over_limit(len(control), MAX_CONTROL_EVENTS, "Control event count")
                return control
    return []


def resolve_header(payload: dict[str, Any], bv_path: Path) -> dict[str, Any]:
    header: dict[str, Any] = {}
    header.update(sibling_header(bv_path))

    raw_header = payload.get("header")
    if isinstance(raw_header, dict):
        header.update(raw_header)
    elif "header" in payload:
        raise BvMidiError("header must be an object when provided.")

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
        if time < previous_time:
            raise BvMidiError("tempoMap must be sorted by time.")
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
        if time < previous_time:
            raise BvMidiError("timeSignatureMap must be sorted by time.")
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
    segments: list[TimeSignatureSegment] = []
    for index, item in enumerate(header["timeSignatureMap"]):
        start = float(item["time"])
        numerator = int(item["numerator"])
        denominator = int(item["denominator"])
        bar_length = numerator * 4 / denominator

        if index > 0:
            previous = segments[-1]
            elapsed = start - previous.start
            bars_elapsed = elapsed / previous.bar_length
            rounded = round(bars_elapsed)
            if abs(bars_elapsed - rounded) > 1e-6:
                raise BvMidiError(
                    "Time signature change is not aligned to the previous bar boundary: "
                    f"time={normalize_number(start)} beats."
                )

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
    total_notes = 0
    for track_name in EXPECTED_TRACKS:
        notes = tracks[track_name]
        if not isinstance(notes, list):
            raise BvMidiError(f"Track {track_name} must be a list.")
        reject_if_over_limit(len(notes), MAX_NOTES_PER_TRACK, f"Track {track_name} note count")
        total_notes += len(notes)
        normalized[track_name] = notes
    reject_if_over_limit(total_notes, MAX_TOTAL_NOTES, "Total BV note count")
    return normalized


def validate_notes(
    tracks: dict[str, list[dict[str, Any]]],
    segments: list[TimeSignatureSegment],
    tolerance: float,
) -> float:
    max_end = 0.0
    for track_name, notes in tracks.items():
        track_notes: list[tuple[float, float, int, dict[str, Any]]] = []
        for note in notes:
            if not isinstance(note, dict):
                raise BvMidiError(f"Track {track_name} contains a non-object note.")
            for key in ("name", "midi", "time", "duration"):
                if key not in note:
                    raise BvMidiError(f"Track {track_name} note is missing '{key}'.")

            label = f"Track {track_name} note"
            midi = require_int(note, "midi", label)
            start = require_number(note, "time", label)
            duration = require_number(note, "duration", label)
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
    return max_end


def note_time(note: dict[str, Any]) -> float:
    return float(note["time"])


def note_duration(note: dict[str, Any]) -> float:
    return float(note["duration"])


def same_timing(left: dict[str, Any], right: dict[str, Any], tolerance: float) -> bool:
    return (
        abs(note_time(left) - note_time(right)) <= tolerance
        and abs(note_duration(left) - note_duration(right)) <= tolerance
    )


def validate_melody_aligned_harmonies(
    tracks: dict[str, list[dict[str, Any]]],
    melody: list[dict[str, Any]] | None,
    tolerance: float,
    control: list[dict[str, Any]] | None = None,
) -> None:
    if melody is None:
        return

    no_bv_spans = [
        (float(item["time"]), float(item["time"]) + float(item["duration"]))
        for item in control or []
        if isinstance(item, dict) and item.get("section") == "no_bv"
    ]

    def requires_harmony(note: dict[str, Any]) -> bool:
        start = float(note["time"])
        return not any(
            span_start - tolerance <= start < span_end - tolerance
            for span_start, span_end in no_bv_spans
        )

    for index, melody_note in enumerate(melody):
        if not isinstance(melody_note, dict):
            raise BvMidiError(f"Source melody note at index {index} must be an object.")
        label = f"Source melody note at index {index}"
        for key in ("name", "midi", "time", "duration"):
            if key not in melody_note:
                raise BvMidiError(f"{label} is missing '{key}'.")
        require_int(melody_note, "midi", label)
        require_number(melody_note, "time", label)
        require_number(melody_note, "duration", label)

    for track_name in ("LeadDouble",):
        if len(tracks[track_name]) != len(melody):
            raise BvMidiError(
                f"{track_name} must contain exactly one note per melody note. "
                f"Expected {len(melody)}, found {len(tracks[track_name])}."
            )

    harmony_melody = [note for note in melody if requires_harmony(note)]
    for track_name in MELODY_ALIGNED_TRACKS:
        if len(tracks[track_name]) != len(harmony_melody):
            raise BvMidiError(
                f"{track_name} must contain exactly one note per harmony-enabled melody note. "
                f"Expected {len(harmony_melody)}, found {len(tracks[track_name])}."
            )

    for index, melody_note in enumerate(melody):
        lead_note = tracks["LeadDouble"][index]
        for key in ("name", "midi", "time", "duration"):
            if lead_note.get(key) != melody_note.get(key):
                raise BvMidiError(
                    "LeadDouble must exactly copy melody. "
                    f"Mismatch at index {index}, field '{key}'."
                )

    for index, melody_note in enumerate(harmony_melody):
        melody_midi = int(melody_note["midi"])
        for track_name in MELODY_ALIGNED_TRACKS:
            harmony_note = tracks[track_name][index]
            if not same_timing(harmony_note, melody_note, tolerance):
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

    sorted_events = sorted(events, key=lambda item: (item[0], item[1]))
    for tick, _, event_payload in sorted_events:
        payload.extend(vlq(tick - previous_tick))
        payload.extend(event_payload)
        previous_tick = tick

    payload.extend(vlq(max(0, end_tick - previous_tick)))
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
    events: list[tuple[int, int, bytes]] = []
    events.append((0, 0, meta_event(0x03, b"conductor")))

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
    overwrite: bool = True,
) -> None:
    if path.exists() and not overwrite:
        raise BvMidiError(f"Output MIDI file already exists: {path}")

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
    end_tick = beats_to_ticks(header_duration, ticks_per_beat)

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
    write_bytes_atomically(path, midi_header + b"".join(chunks))


def convert_bv_raw_to_midi(
    bv_path: Path,
    output_path: Path | None = None,
    ticks_per_beat: int = DEFAULT_TICKS_PER_BEAT,
    velocity: int = 80,
    tolerance: float = 0.01,
    overwrite: bool = True,
) -> Path:
    validate_export_options(ticks_per_beat, velocity, tolerance)
    payload = read_json(bv_path)
    header = resolve_header(payload, bv_path)
    tracks = load_tracks(payload)
    segments = build_time_signature_segments(header)
    max_note_end = validate_notes(tracks, segments, tolerance)
    validate_melody_aligned_harmonies(
        tracks,
        sibling_melody(bv_path),
        tolerance,
        sibling_control(bv_path),
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
        destination = convert_bv_raw_to_midi(
            bv_path,
            output_path,
            args.ticks_per_beat,
            args.velocity,
            args.tolerance,
            overwrite=True,
        )
    except (BvMidiError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote MIDI to {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
