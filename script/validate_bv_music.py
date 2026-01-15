#!/usr/bin/env python3
"""Validate and score a finished bv_raw.json arrangement.

This script is a review aid for the background-vocals skill. It does not
generate or rewrite arrangement content.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import bv_raw_to_midi


EXPECTED_NOTE_KEYS = {"name", "midi", "time", "duration"}
MELODY_ALIGNED_TRACKS = ("HHA", "HHB", "HHF", "LHA", "LHB", "LHF")
CONTOUR_TRACKS = ("HHA", "HHB", "LHA", "LHB")
HIGH_TRACKS = ("HHA", "HHB", "HHF")
LOW_TRACKS = ("LHA", "LHB", "LHF")
PAD_TRACKS = ("VocalPad1", "VocalPad2", "VocalPad3", "VocalPad4")
PITCH_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
ROOTS = {
    "C": 0,
    "B#": 0,
    "C#": 1,
    "Db": 1,
    "D": 2,
    "D#": 3,
    "Eb": 3,
    "E": 4,
    "Fb": 4,
    "E#": 5,
    "F": 5,
    "F#": 6,
    "Gb": 6,
    "G": 7,
    "G#": 8,
    "Ab": 8,
    "A": 9,
    "A#": 10,
    "Bb": 10,
    "B": 11,
    "Cb": 11,
}
CHORD_QUALITY_INTERVALS = {
    "": {0, 4, 7},
    "m": {0, 3, 7},
    "dim": {0, 3, 6},
    "aug": {0, 4, 8},
    "sus": {0, 5, 7},
    "sus2": {0, 2, 7},
    "sus4": {0, 5, 7},
    "add2": {0, 2, 4, 7},
    "add9": {0, 2, 4, 7},
    "6": {0, 4, 7, 9},
    "7": {0, 4, 7, 10},
    "9": {0, 2, 4, 7, 10},
    "11": {0, 2, 4, 5, 7, 10},
    "13": {0, 2, 4, 5, 7, 9, 10},
    "m6": {0, 3, 7, 9},
    "m7": {0, 3, 7, 10},
    "m7b5": {0, 3, 6, 10},
    "m9": {0, 2, 3, 7, 10},
    "m11": {0, 2, 3, 5, 7, 10},
    "m13": {0, 2, 3, 5, 7, 9, 10},
    "maj7": {0, 4, 7, 11},
    "maj9": {0, 2, 4, 7, 11},
    "maj11": {0, 2, 4, 5, 7, 11},
    "maj13": {0, 2, 4, 5, 7, 9, 11},
    "dim7": {0, 3, 6, 9},
    "aug7": {0, 4, 8, 10},
    "7b9": {0, 1, 4, 7, 10},
    "7sus2": {0, 2, 7, 10},
    "7sus4": {0, 5, 7, 10},
}


@dataclass(frozen=True)
class TimeSignatureSegment:
    start: float
    numerator: int
    denominator: int
    bar_length: float
    start_bar: int


def midi_name(midi: int) -> str:
    return f"{PITCH_NAMES[midi % 12]}{midi // 12 - 1}"


def note_end(note: dict[str, Any]) -> float:
    return float(note["time"]) + float(note["duration"])


def resolve_bv_path(path: Path) -> Path:
    if path.is_dir():
        return path / "bv_raw.json"
    return path


def read_json(path: Path) -> dict[str, Any]:
    return bv_raw_to_midi.read_json(path)


def source_payload(bv_path: Path) -> dict[str, Any]:
    for name in ("melody.json", "canonical.json"):
        candidate = bv_path.parent / name
        if candidate.exists():
            payload = read_json(candidate)
            if not isinstance(payload, dict):
                raise ValueError(f"Source JSON root must be an object: {candidate}")
            if not isinstance(payload.get("melody"), list):
                raise ValueError(f"Source JSON must contain a melody list: {candidate}")
            bv_raw_to_midi.reject_if_over_limit(
                len(payload["melody"]),
                bv_raw_to_midi.MAX_SOURCE_MELODY_NOTES,
                "Source melody note count",
            )
            control = payload.get("control")
            if isinstance(control, list):
                bv_raw_to_midi.reject_if_over_limit(
                    len(control),
                    bv_raw_to_midi.MAX_CONTROL_EVENTS,
                    "Control event count",
                )
            return payload
    raise FileNotFoundError(
        f"Missing required source melody.json or canonical.json beside {bv_path}"
    )


def hard_validate(bv_path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    try:
        payload = bv_raw_to_midi.read_json(bv_path)
        header = bv_raw_to_midi.resolve_header(payload, bv_path)
        tracks = bv_raw_to_midi.load_tracks(payload)
        segments = bv_raw_to_midi.build_time_signature_segments(header)
        bv_raw_to_midi.validate_notes(tracks, segments, 0.01)
        bv_raw_to_midi.validate_melody_aligned_harmonies(
            tracks,
            bv_raw_to_midi.sibling_melody(bv_path),
            0.01,
            bv_raw_to_midi.sibling_control(bv_path),
        )
    except Exception as exc:  # noqa: BLE001 - report all validation failures to the agent.
        errors.append(str(exc))
        return None, errors

    if set(payload.keys()) != {"header", "tracks"}:
        errors.append("bv_raw.json top-level keys must be exactly: header, tracks.")

    for track_name, notes in tracks.items():
        for index, note in enumerate(notes):
            keys = set(note.keys())
            if keys != EXPECTED_NOTE_KEYS:
                errors.append(
                    f"{track_name}[{index}] must contain only name, midi, time, duration; "
                    f"found {sorted(keys)}."
                )
            midi = int(note["midi"])
            if note.get("name") != midi_name(midi):
                errors.append(
                    f"{track_name}[{index}] name/midi mismatch: "
                    f"name={note.get('name')} midi={midi} expected={midi_name(midi)}."
                )

    return {"header": header, "tracks": tracks}, errors


def control_spans(source: dict[str, Any]) -> list[dict[str, Any]]:
    control = source.get("control")
    return control if isinstance(control, list) else []


def section_at(control: list[dict[str, Any]], time: float) -> str:
    for item in control:
        start = float(item["time"])
        end = start + float(item["duration"])
        if start <= time < end:
            return str(item.get("section", "default"))
    return "default"


def build_time_signature_segments(header: dict[str, Any]) -> list[TimeSignatureSegment]:
    time_sigs = header.get("timeSignatureMap") or [
        {"time": 0, "numerator": 4, "denominator": 4}
    ]
    segments: list[TimeSignatureSegment] = []
    current_start_bar = 1

    for index, item in enumerate(time_sigs):
        start = float(item["time"])
        numerator = int(item["numerator"])
        denominator = int(item["denominator"])
        bar_length = numerator * 4 / denominator

        if index > 0:
            previous = segments[-1]
            elapsed = max(0.0, start - previous.start)
            current_start_bar = previous.start_bar + round(elapsed / previous.bar_length)

        segments.append(
            TimeSignatureSegment(
                start=start,
                numerator=numerator,
                denominator=denominator,
                bar_length=bar_length,
                start_bar=current_start_bar,
            )
        )

    return segments


def segment_for_time(
    segments: list[TimeSignatureSegment],
    time: float,
) -> TimeSignatureSegment:
    selected = segments[0]
    for segment in segments:
        if time + 1e-9 >= segment.start:
            selected = segment
        else:
            break
    return selected


def segment_for_bar(
    segments: list[TimeSignatureSegment],
    bar: int,
) -> TimeSignatureSegment:
    selected = segments[0]
    for segment in segments:
        if bar >= segment.start_bar:
            selected = segment
        else:
            break
    return selected


def bar_key(time: float, segments: list[TimeSignatureSegment]) -> int:
    segment = segment_for_time(segments, time)
    offset = max(0.0, time - segment.start)
    return segment.start_bar + int(offset // segment.bar_length)


def bar_beat_to_time(
    bar: int,
    beat: float,
    segments: list[TimeSignatureSegment],
) -> float:
    segment = segment_for_bar(segments, bar)
    return segment.start + (bar - segment.start_bar) * segment.bar_length + (beat - 1)


def chord_pitch_classes(chord_name: str) -> set[int] | None:
    chord = chord_name.split("(possible alternative:", 1)[0].strip()
    if chord == "N.C.":
        return None
    match = re.match(r"^([A-G](?:#|b)?)(.*)$", chord)
    if not match:
        raise ValueError(f"Unsupported chord name: {chord_name}")
    root_name, quality = match.groups()
    root = ROOTS.get(root_name)
    intervals = CHORD_QUALITY_INTERVALS.get(quality)
    if root is None or intervals is None:
        raise ValueError(f"Unsupported chord name: {chord_name}")
    return {(root + pc) % 12 for pc in intervals}


def expected_bar_count(header: dict[str, Any], segments: list[TimeSignatureSegment]) -> int:
    duration = float(header["duration"])
    if duration <= 0:
        return 0
    return bar_key(max(0.0, duration - 1e-9), segments)


def expected_tempo_markers(
    header: dict[str, Any],
    segments: list[TimeSignatureSegment],
) -> dict[int, float]:
    markers: dict[int, float] = {}
    for item in header["tempoMap"]:
        bar = bar_key(float(item["time"]), segments)
        bpm = float(item["bpm"])
        if bar in markers and markers[bar] != bpm:
            raise ValueError(f"Multiple BPM changes in bar {bar} cannot be represented in note.txt.")
        markers[bar] = bpm
    return markers


def validate_markers(
    actual: dict[int, int | float | tuple[int, int]],
    expected: dict[int, int | float | tuple[int, int]],
    label: str,
) -> None:
    if actual != expected:
        raise ValueError(f"note.txt {label} markers do not match header: expected={expected}, found={actual}")


def chord_events(note_txt: Path, header: dict[str, Any]) -> list[tuple[float, set[int] | None, str]]:
    if not note_txt.exists():
        raise FileNotFoundError(f"Missing required note.txt: {note_txt}")
    if not note_txt.is_file():
        raise ValueError(f"note.txt path is not a file: {note_txt}")
    events: list[tuple[float, set[int] | None, str]] = []
    segments = build_time_signature_segments(header)

    lines = note_txt.read_text(encoding="utf-8").splitlines()
    non_empty = [line.strip() for line in lines if line.strip()]
    if not non_empty or non_empty[0] != "Time Unit: beats":
        raise ValueError("note.txt first non-empty line must be 'Time Unit: beats'.")

    bar_pattern = re.compile(r"^Bar\s+(\d+):")
    event_pattern = re.compile(r"beat\s+([0-9.]+)\s+([^|]+)")
    time_signature_pattern = re.compile(r"Time Signature\s+(\d+)/(\d+)")
    bpm_pattern = re.compile(r"BPM\s+([0-9]+(?:\.[0-9]+)?)")
    chord_bars: list[int] = []
    time_signature_markers: dict[int, tuple[int, int]] = {}
    tempo_markers: dict[int, float] = {}

    for line in lines:
        stripped = line.strip()
        bar_match = bar_pattern.search(stripped)
        if not bar_match:
            continue
        bar = int(bar_match.group(1))
        if bar < 1:
            raise ValueError(f"note.txt bar must be at least 1: {bar}")
        segment = segment_for_bar(segments, bar)
        time_signature_match = time_signature_pattern.search(stripped)
        if time_signature_match:
            if bar in time_signature_markers:
                raise ValueError(f"note.txt contains duplicate time signature marker for bar {bar}.")
            time_signature_markers[bar] = (
                int(time_signature_match.group(1)),
                int(time_signature_match.group(2)),
            )
        bpm_match = bpm_pattern.search(stripped)
        if bpm_match:
            if bar in tempo_markers:
                raise ValueError(f"note.txt contains duplicate BPM marker for bar {bar}.")
            tempo_markers[bar] = float(bpm_match.group(1))

        line_events = 0
        for part in stripped.split("|"):
            match = event_pattern.search(part.strip())
            if not match:
                continue
            line_events += 1
            beat = float(match.group(1))
            if beat < 1 or beat >= 1 + segment.bar_length:
                raise ValueError(
                    f"note.txt beat {beat:g} is outside bar {bar} "
                    f"for time signature {segment.numerator}/{segment.denominator}."
                )
            chord = match.group(2).strip()
            time = bar_beat_to_time(bar, beat, segments)
            duration = float(header.get("duration", 0))
            if time < 0 or time >= duration:
                raise ValueError(
                    f"note.txt chord event at bar {bar} beat {beat:g} "
                    f"is outside header.duration={duration:g}."
                )
            events.append((time, chord_pitch_classes(chord), chord))
        if line_events:
            chord_bars.append(bar)

    if not events:
        raise ValueError(f"note.txt does not contain any chord events: {note_txt}")

    bar_count = expected_bar_count(header, segments)
    expected_bars = list(range(1, bar_count + 1))
    if chord_bars != expected_bars:
        raise ValueError(
            f"note.txt chord bars must be complete and ordered: "
            f"expected bars 1-{bar_count}, found={chord_bars[:16]}"
        )

    expected_time_signatures = {
        segment.start_bar: (segment.numerator, segment.denominator) for segment in segments
    }
    validate_markers(time_signature_markers, expected_time_signatures, "time signature")
    validate_markers(tempo_markers, expected_tempo_markers(header, segments), "BPM")
    return sorted(events, key=lambda item: item[0])


def active_chord(events: list[tuple[float, set[int] | None, str]], time: float) -> tuple[set[int] | None, str] | None:
    selected: tuple[set[int] | None, str] | None = None
    for event_time, pcs, name in events:
        if event_time <= time + 1e-9:
            selected = (pcs, name)
        else:
            break
    return selected


def score_arrangement(
    bv_path: Path,
    payload: dict[str, Any],
    source: dict[str, Any],
) -> tuple[int, list[str], list[str]]:
    warnings: list[str] = []
    suggestions: list[str] = []
    deductions = 0
    header = payload["header"]
    tracks = payload["tracks"]
    melody = source.get("melody") if isinstance(source.get("melody"), list) else []
    control = control_spans(source)
    segments = build_time_signature_segments(header)

    for track_name in CONTOUR_TRACKS:
        by_bar: dict[int, list[int]] = {}
        for note in tracks[track_name]:
            section = section_at(control, float(note["time"]))
            if section == "no_bv":
                continue
            by_bar.setdefault(bar_key(float(note["time"]), segments), []).append(int(note["midi"]))
        repeated_bars = [
            bar
            for bar, midis in by_bar.items()
            if len(midis) >= 4 and len(set(midis)) <= max(1, len(midis) // 4)
        ]
        if repeated_bars:
            warnings.append(
                f"{track_name} has low contour variety in bars {repeated_bars[:8]}."
            )
            suggestions.append(f"Revise {track_name} so it follows more of the melody contour.")
            deductions += min(10, len(repeated_bars))

    for track_name in MELODY_ALIGNED_TRACKS:
        notes = tracks[track_name]
        leaps = [
            abs(int(notes[index]["midi"]) - int(notes[index - 1]["midi"]))
            for index in range(1, len(notes))
        ]
        large_leaps = [leap for leap in leaps if leap > 7]
        if large_leaps:
            warnings.append(f"{track_name} has {len(large_leaps)} leaps larger than a fifth.")
            suggestions.append(f"Smooth large jumps in {track_name} unless they mark a phrase boundary.")
            deductions += min(8, len(large_leaps))

    if tracks["HHA"] and tracks["HHB"]:
        paired = zip(tracks["HHA"], tracks["HHB"])
        duplicate = sum(1 for left, right in paired if int(left["midi"]) == int(right["midi"]))
        rate = duplicate / max(1, min(len(tracks["HHA"]), len(tracks["HHB"])))
        if rate > 0.2:
            warnings.append(f"HHA and HHB duplicate each other on {rate:.0%} of aligned notes.")
            suggestions.append("Separate HHA and HHB into distinct upper harmony functions.")
            deductions += min(12, round(rate * 20))

    if melody:
        harmony_melody = [
            note
            for note in melody
            if section_at(control, float(note["time"])) != "no_bv"
        ]
        for track_name in HIGH_TRACKS:
            wide = sum(
                1
                for melody_note, harmony_note in zip(harmony_melody, tracks[track_name])
                if int(harmony_note["midi"]) - int(melody_note["midi"]) > 12
            )
            if wide:
                warnings.append(f"{track_name} sits more than an octave above melody on {wide} notes.")
                deductions += min(6, wide)
        for track_name in LOW_TRACKS:
            wide = sum(
                1
                for melody_note, harmony_note in zip(harmony_melody, tracks[track_name])
                if int(melody_note["midi"]) - int(harmony_note["midi"]) > 12
            )
            if wide:
                warnings.append(f"{track_name} sits more than an octave below melody on {wide} notes.")
                deductions += min(6, wide)

    events = chord_events(bv_path.parent / "note.txt", header)
    chord_misses = 0
    if events:
        for track_name in (*MELODY_ALIGNED_TRACKS, *PAD_TRACKS):
            for note in tracks[track_name]:
                chord = active_chord(events, float(note["time"]))
                if chord is None:
                    continue
                pcs, chord_name = chord
                if pcs is not None and int(note["midi"]) % 12 not in pcs:
                    chord_misses += 1
                    if chord_misses <= 12:
                        warnings.append(
                            f"{track_name} note {note['name']} at {note['time']} is outside {chord_name}."
                        )
        if chord_misses:
            suggestions.append(
                "Review non-chord tones. Keep intentional tensions, but correct accidental misses."
            )
            deductions += min(15, chord_misses)

    if not warnings:
        suggestions.append("No soft music issues detected by the current heuristic checks.")

    return max(0, 100 - deductions), warnings, suggestions


def validate(path: Path, min_score: int) -> tuple[dict[str, Any], int]:
    bv_path = resolve_bv_path(path)
    payload, hard_errors = hard_validate(bv_path)
    if payload is None:
        report = {
            "passed": False,
            "score": 0,
            "hardErrors": hard_errors,
            "warnings": [],
            "suggestions": ["Fix hard validation errors before revising musical details."],
        }
        return report, 1

    try:
        source = source_payload(bv_path)
    except Exception as exc:  # noqa: BLE001 - keep validator output machine-readable.
        report = {
            "passed": False,
            "score": 0,
            "minScore": min_score,
            "hardErrors": [*hard_errors, f"Unable to read source melody/canonical JSON: {exc}"],
            "warnings": [],
            "suggestions": ["Fix source JSON files before revising musical details."],
        }
        return report, 1

    try:
        score, warnings, suggestions = score_arrangement(bv_path, payload, source)
    except Exception as exc:  # noqa: BLE001 - report soft-scoring side input failures.
        report = {
            "passed": False,
            "score": 0,
            "minScore": min_score,
            "hardErrors": [*hard_errors, f"Unable to score arrangement: {exc}"],
            "warnings": [],
            "suggestions": ["Fix sidecar files used by soft scoring, then rerun validation."],
        }
        return report, 1

    passed = not hard_errors and score >= min_score
    report = {
        "passed": passed,
        "score": score,
        "minScore": min_score,
        "hardErrors": hard_errors,
        "warnings": warnings,
        "suggestions": suggestions,
    }
    return report, 0 if passed else 1


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate and score bv_raw.json.")
    parser.add_argument("path", help="Path to output/<work_name>/ or bv_raw.json.")
    parser.add_argument("--min-score", type=int, default=80, help="Minimum passing score.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    report, exit_code = validate(Path(args.path).expanduser(), args.min_score)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
