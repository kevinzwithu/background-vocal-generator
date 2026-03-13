#!/usr/bin/env python3
"""Convert project chord.json into a complete bar-by-bar note.txt chord chart."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import project_common
except ModuleNotFoundError:
    from script import project_common


MAX_JSON_FILE_BYTES = 5 * 1024 * 1024
MAX_NOTE_TXT_FILE_BYTES = 100 * 1024
MAX_BARS = project_common.MAX_BARS
MAX_SECONDS = project_common.MAX_SECONDS
MAX_CHORD_NOTES = 50_000
MAX_TEMPO_EVENTS = project_common.MAX_TEMPO_EVENTS
MAX_TIME_SIGNATURE_EVENTS = project_common.MAX_TIME_SIGNATURE_EVENTS
MAX_TIME_SIGNATURE_BYTE = project_common.MAX_TIME_SIGNATURE_BYTE
EPSILON = project_common.EPSILON
FLAT_ROOT_NAMES = ("C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B")
SHARP_ROOT_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
CHORD_TEMPLATES = (
    ("13", frozenset({0, 2, 4, 5, 7, 9, 10})),
    ("maj13", frozenset({0, 2, 4, 5, 7, 9, 11})),
    ("m13", frozenset({0, 2, 3, 5, 7, 9, 10})),
    ("11", frozenset({0, 2, 4, 5, 7, 10})),
    ("maj11", frozenset({0, 2, 4, 5, 7, 11})),
    ("m11", frozenset({0, 2, 3, 5, 7, 10})),
    ("9", frozenset({0, 2, 4, 7, 10})),
    ("maj9", frozenset({0, 2, 4, 7, 11})),
    ("m9", frozenset({0, 2, 3, 7, 10})),
    ("7b9", frozenset({0, 1, 4, 7, 10})),
    ("7b9(no5)", frozenset({0, 1, 4, 10})),
    ("7sus2", frozenset({0, 2, 7, 10})),
    ("7sus4", frozenset({0, 5, 7, 10})),
    ("6add9", frozenset({0, 2, 4, 7, 9})),
    ("add9", frozenset({0, 2, 4, 7})),
    ("6", frozenset({0, 4, 7, 9})),
    ("7", frozenset({0, 4, 7, 10})),
    ("maj7", frozenset({0, 4, 7, 11})),
    ("m6", frozenset({0, 3, 7, 9})),
    ("m7", frozenset({0, 3, 7, 10})),
    ("m7(no5)", frozenset({0, 3, 10})),
    ("m7b5", frozenset({0, 3, 6, 10})),
    ("dim7", frozenset({0, 3, 6, 9})),
    ("aug7", frozenset({0, 4, 8, 10})),
    ("", frozenset({0, 4, 7})),
    ("m", frozenset({0, 3, 7})),
    ("dim", frozenset({0, 3, 6})),
    ("aug", frozenset({0, 4, 8})),
    ("sus2", frozenset({0, 2, 7})),
    ("sus4", frozenset({0, 5, 7})),
    ("5", frozenset({0, 7})),
)


class ChordChartError(Exception):
    pass


@dataclass(frozen=True)
class TimeSignatureSegment:
    start: float
    numerator: int
    denominator: int
    bar_length: float
    start_bar: int


@dataclass(frozen=True)
class ChordCandidate:
    root: int
    quality: str
    exact: bool


@dataclass(frozen=True)
class ChordEvent:
    time: float
    bar: int
    beat: float
    name: str


@dataclass(frozen=True)
class ConversionResult:
    output_path: Path
    bar_count: int
    event_count: int
    warnings: tuple[str, ...]


normalize_number = project_common.normalize_number


def require_number(item: dict[str, Any], key: str, label: str) -> float:
    if key not in item or isinstance(item[key], bool):
        raise ChordChartError(f"{label}.{key} must be a finite number.")
    try:
        value = float(item[key])
    except (TypeError, ValueError, OverflowError) as exc:
        raise ChordChartError(f"{label}.{key} must be a finite number.") from exc
    if not math.isfinite(value):
        raise ChordChartError(f"{label}.{key} must be a finite number.")
    return value


def require_int(item: dict[str, Any], key: str, label: str) -> int:
    value = require_number(item, key, label)
    if not value.is_integer():
        raise ChordChartError(f"{label}.{key} must be an integer.")
    return int(value)


def read_payload(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            raise ChordChartError(f"Input chord.json does not exist: {path}")
        if not path.is_file():
            raise ChordChartError(f"Input chord.json path is not a file: {path}")
        if path.stat().st_size > MAX_JSON_FILE_BYTES:
            raise ChordChartError(f"chord.json exceeds {MAX_JSON_FILE_BYTES} bytes.")
        with path.open("rb") as source:
            raw = source.read(MAX_JSON_FILE_BYTES + 1)
    except OSError as exc:
        raise ChordChartError(f"Unable to inspect or read chord.json {path}: {exc}") from exc
    if len(raw) > MAX_JSON_FILE_BYTES:
        raise ChordChartError(f"chord.json exceeds {MAX_JSON_FILE_BYTES} bytes.")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ChordChartError(f"chord.json must use UTF-8 encoding: {path}") from exc
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ChordChartError(f"Invalid chord.json {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ChordChartError("chord.json root must be an object.")
    return payload


def validate_header(payload: dict[str, Any]) -> dict[str, Any]:
    header = payload.get("header")
    if not isinstance(header, dict):
        raise ChordChartError("chord.json must contain a header object.")
    if header.get("timeUnit") != "beats":
        raise ChordChartError("header.timeUnit must be 'beats'.")
    start_time = require_number(header, "startTime", "header")
    duration = require_number(header, "duration", "header")
    if abs(start_time) > EPSILON:
        raise ChordChartError("header.startTime must be 0.")
    if duration <= 0:
        raise ChordChartError("header.duration must be positive.")
    validate_tempo_map(header.get("tempoMap"))
    validate_time_signature_map(header.get("timeSignatureMap"))
    for map_name in ("tempoMap", "timeSignatureMap"):
        for item in header[map_name]:
            event_time = float(item["time"])
            if event_time >= duration:
                raise ChordChartError(
                    f"{map_name} event at time={format_number(event_time)} "
                    "must occur before header.duration."
                )
    return header


def validate_tempo_map(tempo_map: Any) -> None:
    if not isinstance(tempo_map, list) or not tempo_map:
        raise ChordChartError("header.tempoMap must be a non-empty list.")
    if len(tempo_map) > MAX_TEMPO_EVENTS:
        raise ChordChartError(f"Tempo event count exceeds {MAX_TEMPO_EVENTS}.")
    previous_time = -1.0
    for index, item in enumerate(tempo_map):
        if not isinstance(item, dict):
            raise ChordChartError(f"tempoMap[{index}] must be an object.")
        time = require_number(item, "time", f"tempoMap[{index}]")
        bpm = require_number(item, "bpm", f"tempoMap[{index}]")
        if time < 0 or time <= previous_time:
            raise ChordChartError("tempoMap times must be non-negative and strictly increasing.")
        if bpm <= 0:
            raise ChordChartError("tempoMap bpm must be positive.")
        previous_time = time
    if abs(float(tempo_map[0]["time"])) > EPSILON:
        raise ChordChartError("tempoMap must start at time 0.")


def validate_time_signature_map(time_signature_map: Any) -> None:
    if not isinstance(time_signature_map, list) or not time_signature_map:
        raise ChordChartError("header.timeSignatureMap must be a non-empty list.")
    if len(time_signature_map) > MAX_TIME_SIGNATURE_EVENTS:
        raise ChordChartError(
            f"Time signature event count exceeds {MAX_TIME_SIGNATURE_EVENTS}."
        )
    previous_time = -1.0
    for index, item in enumerate(time_signature_map):
        if not isinstance(item, dict):
            raise ChordChartError(f"timeSignatureMap[{index}] must be an object.")
        label = f"timeSignatureMap[{index}]"
        time = require_number(item, "time", label)
        numerator = require_int(item, "numerator", label)
        denominator = require_int(item, "denominator", label)
        if time < 0 or time <= previous_time:
            raise ChordChartError(
                "timeSignatureMap times must be non-negative and strictly increasing."
            )
        if numerator <= 0:
            raise ChordChartError("timeSignatureMap numerator must be positive.")
        if numerator > MAX_TIME_SIGNATURE_BYTE:
            raise ChordChartError(
                f"timeSignatureMap numerator must be at most {MAX_TIME_SIGNATURE_BYTE}."
            )
        if denominator <= 0 or denominator & (denominator - 1):
            raise ChordChartError("timeSignatureMap denominator must be a power of two.")
        if denominator.bit_length() - 1 > MAX_TIME_SIGNATURE_BYTE:
            raise ChordChartError(
                "timeSignatureMap denominator exponent must be at most "
                f"{MAX_TIME_SIGNATURE_BYTE}."
            )
        previous_time = time
    if abs(float(time_signature_map[0]["time"])) > EPSILON:
        raise ChordChartError("timeSignatureMap must start at time 0.")


def build_segments(header: dict[str, Any]) -> list[TimeSignatureSegment]:
    segments: list[TimeSignatureSegment] = []
    start_bar = 1
    for index, item in enumerate(header["timeSignatureMap"]):
        start = float(item["time"])
        numerator = int(item["numerator"])
        denominator = int(item["denominator"])
        bar_length = numerator * 4 / denominator
        if index:
            previous = segments[-1]
            bars_elapsed = (start - previous.start) / previous.bar_length
            rounded = round(bars_elapsed)
            if abs(bars_elapsed - rounded) > 1e-6:
                raise ChordChartError(
                    "Time signature change is not aligned to a bar boundary: "
                    f"time={normalize_number(start)}."
                )
            start_bar = previous.start_bar + rounded
        segments.append(
            TimeSignatureSegment(start, numerator, denominator, bar_length, start_bar)
        )
    return segments


def bar_and_beat(segments: list[TimeSignatureSegment], time: float) -> tuple[int, float]:
    segment = project_common.segment_for_time(segments, time)
    offset = time - segment.start
    bar_offset = math.floor((offset + EPSILON) / segment.bar_length)
    beat = offset - bar_offset * segment.bar_length + 1
    return segment.start_bar + bar_offset, beat


def format_number(value: float | int) -> str:
    return str(normalize_number(float(value)))


def root_name(root: int, quality: str) -> str:
    if quality in {"dim", "dim7"} and root in {1, 6, 8, 10}:
        return SHARP_ROOT_NAMES[root]
    return FLAT_ROOT_NAMES[root]


def candidate_name(candidate: ChordCandidate) -> str:
    return f"{root_name(candidate.root, candidate.quality)}{candidate.quality}"


def chord_candidates(pitch_classes: frozenset[int]) -> list[ChordCandidate]:
    candidates: list[ChordCandidate] = []
    for root in range(12):
        relative = frozenset((pitch_class - root) % 12 for pitch_class in pitch_classes)
        for quality, intervals in CHORD_TEMPLATES:
            if relative == intervals:
                candidates.append(ChordCandidate(root, quality, True))
    if candidates or len(pitch_classes) != 2:
        return candidates

    for root in pitch_classes:
        relative = frozenset((pitch_class - root) % 12 for pitch_class in pitch_classes)
        if relative == {0, 4}:
            candidates.append(ChordCandidate(root, "", False))
        elif relative == {0, 3}:
            candidates.append(ChordCandidate(root, "m", False))
    return candidates


def name_chord(notes: list[dict[str, Any]], bar: int, beat: float) -> tuple[str, str | None]:
    pitch_classes = frozenset(int(note["midi"]) % 12 for note in notes)
    candidates = chord_candidates(pitch_classes)
    if not candidates:
        pcs = ",".join(str(pc) for pc in sorted(pitch_classes))
        raise ChordChartError(
            f"Unsupported chord pitch-class set at bar {bar} beat {format_number(beat)}: {pcs}."
        )

    lowest_pitch_class = min(int(note["midi"]) for note in notes) % 12
    candidates.sort(
        key=lambda candidate: (
            not candidate.exact,
            candidate.root != lowest_pitch_class,
            candidate_name(candidate),
        )
    )
    selected = candidates[0]
    alternatives = sorted(
        {
            candidate_name(candidate)
            for candidate in candidates[1:]
            if candidate_name(candidate) != candidate_name(selected)
        }
    )
    warning: str | None = None
    if not selected.exact or alternatives:
        detail = f"; alternatives: {', '.join(alternatives)}" if alternatives else ""
        warning = (
            f"Review bar {bar} beat {format_number(beat)}: selected "
            f"{candidate_name(selected)} from incomplete or ambiguous pitch classes{detail}."
        )
    return candidate_name(selected), warning


def validate_and_group_chords(
    payload: dict[str, Any],
    header: dict[str, Any],
    segments: list[TimeSignatureSegment],
) -> tuple[list[ChordEvent], list[str]]:
    raw_chords = payload.get("chord")
    if not isinstance(raw_chords, list):
        raise ChordChartError("chord.json must contain a chord list.")
    if len(raw_chords) > MAX_CHORD_NOTES:
        raise ChordChartError(f"Chord note count exceeds {MAX_CHORD_NOTES}.")

    groups: dict[float, list[dict[str, Any]]] = defaultdict(list)
    duration = float(header["duration"])
    for index, note in enumerate(raw_chords):
        if not isinstance(note, dict):
            raise ChordChartError(f"chord[{index}] must be an object.")
        time = require_number(note, "time", f"chord[{index}]")
        midi = require_int(note, "midi", f"chord[{index}]")
        if time < 0 or time >= duration:
            raise ChordChartError(f"chord[{index}].time must be within header.duration.")
        if not 0 <= midi <= 127:
            raise ChordChartError(f"chord[{index}].midi must be between 0 and 127.")
        groups[time].append(note)

    events: list[ChordEvent] = []
    warnings: list[str] = []
    for time in sorted(groups):
        bar, beat = bar_and_beat(segments, time)
        name, warning = name_chord(groups[time], bar, beat)
        events.append(ChordEvent(time, bar, beat, name))
        if warning:
            warnings.append(warning)
    return events, warnings


def marker_maps(
    header: dict[str, Any],
    segments: list[TimeSignatureSegment],
) -> tuple[dict[int, tuple[int, int]], dict[int, float]]:
    signatures: dict[int, tuple[int, int]] = {}
    for segment in segments:
        signatures[segment.start_bar] = (segment.numerator, segment.denominator)

    tempos: dict[int, float] = {}
    for item in header["tempoMap"]:
        time = float(item["time"])
        bar, beat = bar_and_beat(segments, time)
        if abs(beat - 1) > 1e-6:
            raise ChordChartError(
                "BPM change is not aligned to a bar boundary and cannot be represented "
                f"in note.txt: time={format_number(time)}."
            )
        if bar in tempos:
            raise ChordChartError(f"Multiple BPM changes map to bar {bar}.")
        tempos[bar] = float(item["bpm"])
    return signatures, tempos


def render_chart(
    header: dict[str, Any],
    segments: list[TimeSignatureSegment],
    events: list[ChordEvent],
) -> tuple[str, int]:
    duration = float(header["duration"])
    bar_count, seconds = project_common.piece_metrics(
        duration,
        segments,
        header["tempoMap"],
    )
    if bar_count > MAX_BARS:
        raise ChordChartError(f"Chord chart exceeds {MAX_BARS} bars. Found {bar_count}.")
    if seconds > MAX_SECONDS:
        raise ChordChartError(
            f"Chord chart exceeds 10 minutes. Found {round(seconds, 2)} seconds."
        )
    signatures, tempos = marker_maps(header, segments)
    events_by_bar: dict[int, list[ChordEvent]] = defaultdict(list)
    for event in events:
        events_by_bar[event.bar].append(event)

    lines = ["Time Unit: beats", ""]
    for bar in range(1, bar_count + 1):
        marker_parts: list[str] = []
        if bar in signatures:
            numerator, denominator = signatures[bar]
            marker_parts.append(f"Time Signature {numerator}/{denominator}")
        if bar in tempos:
            marker_parts.append(f"BPM {format_number(tempos[bar])}")
        if marker_parts:
            if bar != 1:
                lines.append("")
            lines.append(f"Bar {bar}: {', '.join(marker_parts)}")

        bar_events = events_by_bar.get(bar)
        if not bar_events:
            lines.append(f"Bar {bar}: beat 1 N.C.")
            continue
        entries = [
            f"beat {format_number(event.beat)} {event.name}"
            for event in sorted(bar_events, key=lambda event: event.time)
        ]
        lines.append(f"Bar {bar}: {' | '.join(entries)}")

    text = "\n".join(lines) + "\n"
    if len(text.encode("utf-8")) > MAX_NOTE_TXT_FILE_BYTES:
        raise ChordChartError(f"Generated note.txt exceeds {MAX_NOTE_TXT_FILE_BYTES} bytes.")
    return text, bar_count


def write_text_atomically(path: Path, text: str, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise ChordChartError(f"Output note.txt already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
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
        if overwrite:
            temp_path.replace(path)
        else:
            try:
                os.link(temp_path, path)
            except FileExistsError as exc:
                raise ChordChartError(f"Output note.txt already exists: {path}") from exc
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def resolve_input_path(path: Path) -> Path:
    return path / "chord.json" if path.is_dir() else path


def convert_chord_json_to_note(
    input_path: Path,
    output_path: Path | None = None,
    overwrite: bool = True,
) -> ConversionResult:
    chord_path = resolve_input_path(input_path)
    payload = read_payload(chord_path)
    header = validate_header(payload)
    segments = build_segments(header)
    events, warnings = validate_and_group_chords(payload, header, segments)
    text, bar_count = render_chart(header, segments, events)
    destination = output_path or chord_path.parent / "note.txt"
    write_text_atomically(destination, text, overwrite)
    return ConversionResult(destination, bar_count, len(events), tuple(warnings))


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert chord.json to note.txt.")
    parser.add_argument("chord_json", help="Path to chord.json or its work directory.")
    parser.add_argument("-o", "--output", help="Output path. Default: sibling note.txt.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing note.txt without prompting.",
    )
    return parser.parse_args(argv)


def confirm_overwrite(path: Path) -> bool:
    try:
        answer = input(f"Output note.txt already exists: {path}\nOverwrite? [y/N]: ")
    except EOFError:
        return False
    return answer.strip().lower() == "y"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    input_path = Path(args.chord_json).expanduser()
    chord_path = resolve_input_path(input_path)
    destination = (
        Path(args.output).expanduser() if args.output else chord_path.parent / "note.txt"
    )

    try:
        destination_exists = destination.exists()
        if destination_exists and not args.force and not confirm_overwrite(destination):
            print(f"Canceled: output note.txt already exists: {destination}", file=sys.stderr)
            return 2
        result = convert_chord_json_to_note(
            chord_path,
            destination,
            overwrite=args.force or destination_exists,
        )
    except (ChordChartError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(
        f"Wrote {result.output_path}: {result.bar_count} bars, "
        f"{result.event_count} chord events."
    )
    for warning in result.warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
