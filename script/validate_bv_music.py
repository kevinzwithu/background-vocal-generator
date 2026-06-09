#!/usr/bin/env python3
"""Validate and score a finished bv_raw.json arrangement.

This script is a review aid for the background-vocals skill. It does not
generate or rewrite arrangement content.
"""

from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import defaultdict
from itertools import combinations
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import bv_raw_to_midi
except ModuleNotFoundError:
    from script import bv_raw_to_midi
try:
    import project_common
except ModuleNotFoundError:
    from script import project_common


BASE_NOTE_KEYS = {"name", "midi", "time", "duration"}
NON_PAD_NOTE_KEYS = BASE_NOTE_KEYS | {"lyric"}
MAX_NOTE_TXT_FILE_BYTES = 100 * 1024
MAX_BARS = project_common.MAX_BARS
MAX_SECONDS = project_common.MAX_SECONDS
MELODY_ALIGNED_TRACKS = ("HHA", "HHB", "HHF", "LHA", "LHB", "LHF")
CONTOUR_TRACKS = ("HHA", "HHB", "LHA", "LHB")
FLAT_TRACKS = ("HHF", "LHF")
FLAT_REFERENCE_TRACKS = {
    "HHF": ("HHA", "HHB"),
    "LHF": ("LHA", "LHB"),
}
HIGH_TRACKS = ("HHA", "HHB", "HHF")
LOW_TRACKS = ("LHA", "LHB", "LHF")
HARMONY_GROUPS = {"upper": HIGH_TRACKS, "lower": LOW_TRACKS}
PAD_TRACKS = ("VocalPad1", "VocalPad2", "VocalPad3", "VocalPad4")
SCORE_VERSION = "bv-score-v5"
PAD_POLICIES = ("sectional", "optional", "required")
DELIVERY_FLOOR_CAP = 79
COPY_RESET_GAP_BEATS = 2.0
HARMONY_FIT_FLOOR = 0.90
HARMONY_FIT_FULL = 0.98
PAD_FIT_FLOOR = 0.95
PAD_FIT_FULL = 0.99
CONTROLLED_TENSION_MAX_BEATS = 0.5
PAD_ACTIVE_COMPLETENESS_FLOOR = 0.70
CATEGORY_SPECS = {
    "harmonyShape": {"maxScore": 25, "floor": 14},
    "padPresence": {"maxScore": 20, "floor": 10},
    "padWriting": {"maxScore": 20, "floor": 11},
    "harmonicFit": {"maxScore": 20, "floor": 12},
    "structureAndPlayability": {"maxScore": 15, "floor": 8},
}
QUALITY_AXIS_SPECS = {
    "harmonicCorrectness": {"floor": 80},
    "musicalStructure": {"floor": 80},
}
QUALITY_AXIS_REVISION_GUIDANCE = {
    "harmonicCorrectness": "Correct per-track chord fit and unresolved tensions before aesthetic revisions.",
    "musicalStructure": "Revise voice roles, vertical voicing, contour, Pad writing, or section development.",
}
CATEGORY_REVISION_GUIDANCE = {
    "harmonyShape": "Revise the flagged harmony pairs or contours before changing density.",
    "padPresence": "Add, remove, or reshape Pad support in the specific failing sections.",
    "padWriting": "Revise Pad voicing, completeness, register, or chord response.",
    "harmonicFit": "Correct the worst unresolved chord-fit episodes first.",
    "structureAndPlayability": "Revise section contrast, spacing, or playability risks.",
}
PAD_SECTION_TARGETS = {
    "verse": (0.0, 0.45),
    "pre_chorus": (0.15, 0.75),
    "chorus": (0.40, 0.90),
    "bridge": (0.0, 0.75),
    "default": (0.15, 0.65),
}
PAD_REQUIRED_MINIMUMS = {
    "verse": 0.20,
    "pre_chorus": 0.30,
    "chorus": 0.45,
    "bridge": 0.20,
    "default": 0.30,
}
PAD_PREFERRED_RANGES = {
    "VocalPad1": (60, 84),
    "VocalPad2": (55, 76),
    "VocalPad3": (48, 69),
    "VocalPad4": (36, 60),
}
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
    "6add9": {0, 2, 4, 7, 9},
    "7": {0, 4, 7, 10},
    "9": {0, 2, 4, 7, 10},
    "11": {0, 2, 4, 5, 7, 10},
    "13": {0, 2, 4, 5, 7, 9, 10},
    "m6": {0, 3, 7, 9},
    "m7": {0, 3, 7, 10},
    "m7(no5)": {0, 3, 10},
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
    "7b9(no5)": {0, 1, 4, 10},
    "7sus2": {0, 2, 7, 10},
    "7sus4": {0, 5, 7, 10},
    "5": {0, 7},
}


TimeSignatureSegment = project_common.TimeSignatureSegment


@dataclass(frozen=True)
class SectionSpan:
    start: float
    end: float
    section: str


def midi_name(midi: int) -> str:
    return f"{PITCH_NAMES[midi % 12]}{midi // 12 - 1}"


def note_end(note: dict[str, Any]) -> float:
    return float(note["time"]) + float(note["duration"])


def rounded_metric(value: float) -> float:
    return round(value, 6)


def resolve_bv_path(path: Path) -> Path:
    if path.is_dir():
        return path / "bv_raw.json"
    return path


def hard_validate(bv_path: Path, source: dict[str, Any], *, sparse: bool = False) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    try:
        payload = bv_raw_to_midi.read_json(bv_path)
        header = bv_raw_to_midi.resolve_header(payload, source)
        tracks = bv_raw_to_midi.load_tracks(payload)
        segments = bv_raw_to_midi.build_time_signature_segments(header)
        bv_raw_to_midi.validate_piece_limits(header, segments)
        max_note_end = bv_raw_to_midi.validate_notes(tracks, segments, 0.01)
        bv_raw_to_midi.validate_header_bounds(header, max_note_end)
        bv_raw_to_midi.validate_melody_aligned_harmonies(
            tracks,
            source.get("melody"),
            0.01,
            source.get("control", []),
            sparse=sparse,
        )
    except Exception as exc:  # noqa: BLE001 - report all validation failures to the agent.
        errors.append(str(exc))
        return None, errors

    if set(payload.keys()) != {"header", "tracks"}:
        errors.append("bv_raw.json top-level keys must be exactly: header, tracks.")

    for track_name, notes in tracks.items():
        for index, note in enumerate(notes):
            keys = set(note.keys())
            allowed_keys = BASE_NOTE_KEYS if track_name in PAD_TRACKS else NON_PAD_NOTE_KEYS
            if not BASE_NOTE_KEYS.issubset(keys) or not keys.issubset(allowed_keys):
                errors.append(
                    f"{track_name}[{index}] must contain only allowed fields including "
                    f"name, midi, time, duration; "
                    f"found {sorted(keys)}."
                )
            midi = int(note["midi"])
            if note.get("name") != midi_name(midi):
                errors.append(
                    f"{track_name}[{index}] name/midi mismatch: "
                    f"name={note.get('name')} midi={midi} expected={midi_name(midi)}."
                )

    return {"header": header, "tracks": tracks}, errors


def control_spans(source: dict[str, Any]) -> list[SectionSpan]:
    control = source.get("control")
    if not isinstance(control, list):
        return []
    spans: list[SectionSpan] = []
    for index, item in enumerate(control):
        if not isinstance(item, dict):
            raise ValueError(f"Source control item at index {index} must be an object.")
        label = f"Source control item at index {index}"
        start = bv_raw_to_midi.require_number(item, "time", label)
        duration = bv_raw_to_midi.require_number(item, "duration", label)
        if start < 0 or duration <= 0:
            raise ValueError(f"{label} must have non-negative time and positive duration.")
        spans.append(
            SectionSpan(start, start + duration, str(item.get("section", "default")))
        )
    spans.sort(key=lambda span: (span.start, span.end, span.section))
    for previous, current in zip(spans, spans[1:]):
        if current.start < previous.end:
            raise ValueError("Source control spans must not overlap.")
    return spans


def section_at(control: list[SectionSpan], time: float) -> str:
    low = 0
    high = len(control)
    while low < high:
        middle = (low + high) // 2
        if control[middle].start <= time:
            low = middle + 1
        else:
            high = middle
    if low == 0:
        return "default"
    span = control[low - 1]
    return span.section if time < span.end else "default"


def build_time_signature_segments(header: dict[str, Any]) -> list[TimeSignatureSegment]:
    return project_common.build_time_signature_segments(header.get("timeSignatureMap") or [{"time": 0, "numerator": 4, "denominator": 4}])


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
    return project_common.bar_and_beat(segments, time)[0]


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
    return project_common.count_bars(float(header["duration"]), segments)


def read_note_text(note_txt: Path) -> str:
    try:
        exists = note_txt.exists()
        is_file = note_txt.is_file() if exists else False
    except OSError as exc:
        raise ValueError(f"Unable to inspect note.txt {note_txt}: {exc}") from exc
    if not exists:
        raise FileNotFoundError(f"Missing required note.txt: {note_txt}")
    if not is_file:
        raise ValueError(f"note.txt path is not a file: {note_txt}")

    try:
        if note_txt.stat().st_size > MAX_NOTE_TXT_FILE_BYTES:
            raise ValueError(
                f"note.txt file size exceeds limit of {MAX_NOTE_TXT_FILE_BYTES} bytes."
            )
        with note_txt.open("rb") as source:
            raw = source.read(MAX_NOTE_TXT_FILE_BYTES + 1)
    except OSError as exc:
        raise ValueError(f"Unable to inspect or read note.txt {note_txt}: {exc}") from exc
    if len(raw) > MAX_NOTE_TXT_FILE_BYTES:
        raise ValueError(f"note.txt file size exceeds limit of {MAX_NOTE_TXT_FILE_BYTES} bytes.")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"note.txt must use UTF-8 encoding: {note_txt}") from exc


def expected_tempo_markers(
    header: dict[str, Any],
    segments: list[TimeSignatureSegment],
) -> dict[int, float]:
    markers: dict[int, float] = {}
    tempo_map = header["tempoMap"]
    tempo_index = 0
    previous_bpm: float | None = None
    for bar in range(1, expected_bar_count(header, segments) + 1):
        bar_start = bar_beat_to_time(bar, 1, segments)
        while (
            tempo_index + 1 < len(tempo_map)
            and float(tempo_map[tempo_index + 1]["time"]) <= bar_start + 1e-9
        ):
            tempo_index += 1
        bpm = float(tempo_map[tempo_index]["bpm"])
        if bpm != previous_bpm:
            markers[bar] = bpm
            previous_bpm = bpm
    return markers


def validate_markers(
    actual: dict[int, int | float | tuple[int, int]],
    expected: dict[int, int | float | tuple[int, int]],
    label: str,
) -> None:
    if actual != expected:
        raise ValueError(f"note.txt {label} markers do not match header: expected={expected}, found={actual}")


def chord_events(note_txt: Path, header: dict[str, Any]) -> list[tuple[float, set[int] | None, str]]:
    events: list[tuple[float, set[int] | None, str]] = []
    segments = build_time_signature_segments(header)
    duration = float(header["duration"])
    bar_count, seconds = project_common.piece_metrics(
        duration,
        segments,
        header["tempoMap"],
    )
    if bar_count > MAX_BARS:
        raise ValueError(f"Arrangement length must not exceed {MAX_BARS} bars. Found {bar_count} bars.")
    if seconds > MAX_SECONDS:
        raise ValueError(
            f"Arrangement length must not exceed 10 minutes. Found {round(seconds, 2)} seconds."
        )

    lines = read_note_text(note_txt).splitlines()
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
    low = 0
    high = len(events)
    target = time + 1e-9
    while low < high:
        middle = (low + high) // 2
        if events[middle][0] <= target:
            low = middle + 1
        else:
            high = middle
    if low == 0:
        return None
    _, pcs, name = events[low - 1]
    return pcs, name


def chord_spans(
    events: list[tuple[float, set[int] | None, str]],
    duration: float,
) -> list[tuple[float, float, set[int] | None, str]]:
    spans: list[tuple[float, float, set[int] | None, str]] = []
    for index, (start, pitch_classes, chord_name) in enumerate(events):
        end = duration if index + 1 == len(events) else min(duration, events[index + 1][0])
        if end > start + 1e-9:
            spans.append((start, end, pitch_classes, chord_name))
    return spans


def chord_defining_pitch_classes(chord_name: str) -> set[int]:
    chord = chord_name.split("(possible alternative:", 1)[0].strip()
    match = re.match(r"^([A-G](?:#|b)?)(.*)$", chord)
    if not match:
        return set()
    root_name, quality = match.groups()
    root = ROOTS.get(root_name)
    pitch_classes = chord_pitch_classes(chord)
    if root is None or pitch_classes is None:
        return set()
    if quality == "5":
        return set(pitch_classes)
    defining = set(pitch_classes) - {root, (root + 7) % 12}
    return defining or (set(pitch_classes) - {root})


def pad_chord_definition_metrics(
    tracks: dict[str, list[dict[str, Any]]],
    spans: list[tuple[float, float, set[int] | None, str]],
) -> dict[str, Any]:
    note_events: dict[float, list[tuple[int, str, dict[str, Any]]]] = {}
    boundaries: set[float] = set()
    for start, end, _, _ in spans:
        boundaries.update((start, end))
    for track_name in PAD_TRACKS:
        for note in tracks[track_name]:
            start = float(note["time"])
            end = note_end(note)
            boundaries.update((start, end))
            note_events.setdefault(start, []).append((1, track_name, note))
            note_events.setdefault(end, []).append((0, track_name, note))

    active: dict[str, dict[str, Any]] = {}
    supported_beats = 0.0
    defined_beats = 0.0
    supported_segments = 0
    defined_segments = 0
    span_index = 0
    ordered = sorted(boundaries)
    for index, time in enumerate(ordered[:-1]):
        for kind, track_name, note in sorted(
            note_events.get(time, []), key=lambda item: (item[0], item[1])
        ):
            if kind == 0:
                if active.get(track_name) is note:
                    active.pop(track_name, None)
            else:
                active[track_name] = note
        next_time = ordered[index + 1]
        if next_time <= time + 1e-9:
            continue
        midpoint = (time + next_time) / 2
        while span_index < len(spans) and spans[span_index][1] <= midpoint + 1e-9:
            span_index += 1
        if span_index >= len(spans):
            break
        _, _, chord_pcs, chord_name = spans[span_index]
        if chord_pcs is None or len(active) < 3:
            continue
        duration = next_time - time
        supported_beats += duration
        supported_segments += 1
        active_pcs = {int(note["midi"]) % 12 for note in active.values()}
        valid_pcs = active_pcs & chord_pcs
        defining_pcs = chord_defining_pitch_classes(chord_name)
        is_defined = len(valid_pcs) >= 2 and bool(valid_pcs & defining_pcs)
        if is_defined:
            defined_beats += duration
            defined_segments += 1

    return {
        "supportedBeats": rounded_metric(supported_beats),
        "definedBeats": rounded_metric(defined_beats),
        "definedChordSpanRate": rounded_metric(
            defined_beats / max(supported_beats, 1e-9)
        ),
        "supportedSegmentCount": supported_segments,
        "definedSegmentCount": defined_segments,
    }


def merge_non_chord_fragments(
    fragments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge adjacent non-chord slices from one note into musical episodes."""
    episodes: list[dict[str, Any]] = []
    for fragment in fragments:
        if episodes and float(fragment["start"]) <= float(episodes[-1]["end"]) + 1e-9:
            current = episodes[-1]
            current["end"] = max(float(current["end"]), float(fragment["end"]))
            current["beats"] = float(current["beats"]) + float(fragment["beats"])
            current["spans"].append(fragment)
            continue
        episodes.append(
            {
                "start": float(fragment["start"]),
                "end": float(fragment["end"]),
                "beats": float(fragment["beats"]),
                "spans": [fragment],
            }
        )
    return episodes


def chord_episode_label(spans: list[dict[str, Any]]) -> str:
    names = list(dict.fromkeys(str(span["chord"]) for span in spans))
    if len(names) <= 3:
        return " -> ".join(names)
    return " -> ".join(names[:3]) + " -> ..."


def analyze_harmonic_fit(
    tracks: dict[str, list[dict[str, Any]]],
    events: list[tuple[float, set[int] | None, str]],
    duration: float,
) -> tuple[dict[str, Any], list[str], list[str], dict[str, Any]]:
    spans = chord_spans(events, duration)
    fit_beats = 0.0
    non_chord_beats = 0.0
    controlled_tension_beats = 0.0
    nc_beats = 0.0
    issues: list[dict[str, Any]] = []
    per_track: dict[str, dict[str, Any]] = {}
    gate_failures: list[dict[str, Any]] = []
    warnings: list[str] = []
    suggestions: list[str] = []
    active_fit_rates: list[float] = []
    full_score_eligible = True
    has_evaluable_chord = any(pitch_classes is not None for _, _, pitch_classes, _ in spans)

    for track_name in (*MELODY_ALIGNED_TRACKS, *PAD_TRACKS):
        notes = sorted(tracks[track_name], key=lambda note: float(note["time"]))
        span_index = 0
        track_fit = 0.0
        track_non_chord = 0.0
        track_controlled = 0.0
        track_nc = 0.0
        track_issues: list[dict[str, Any]] = []
        for note_index, note in enumerate(notes):
            start = float(note["time"])
            end = min(duration, note_end(note))
            non_chord_fragments: list[dict[str, Any]] = []
            while span_index < len(spans) and spans[span_index][1] <= start + 1e-9:
                span_index += 1
            current_span = span_index
            while current_span < len(spans) and spans[current_span][0] < end - 1e-9:
                chord_start, chord_end, pitch_classes, chord_name = spans[current_span]
                overlap_start = max(start, chord_start)
                overlap_end = min(end, chord_end)
                overlap = overlap_end - overlap_start
                if overlap <= 1e-9:
                    current_span += 1
                    continue
                if pitch_classes is None:
                    nc_beats += overlap
                    track_nc += overlap
                elif int(note["midi"]) % 12 in pitch_classes:
                    fit_beats += overlap
                    track_fit += overlap
                else:
                    non_chord_beats += overlap
                    track_non_chord += overlap
                    non_chord_fragments.append(
                        {
                            "start": overlap_start,
                            "end": overlap_end,
                            "beats": overlap,
                            "chord": chord_name,
                        }
                    )
                current_span += 1

            for episode in merge_non_chord_fragments(non_chord_fragments):
                episode_start = float(episode["start"])
                episode_end = float(episode["end"])
                episode_beats = float(episode["beats"])
                episode_spans = list(episode["spans"])
                resolves_by_step = False
                if (
                    track_name in MELODY_ALIGNED_TRACKS
                    and episode_end >= end - 1e-9
                    and note_index + 1 < len(notes)
                ):
                    next_note = notes[note_index + 1]
                    next_start = float(next_note["time"])
                    next_chord = active_chord(events, next_start)
                    resolves_by_step = (
                        next_start >= episode_end - 1e-9
                        and next_start <= episode_end + CONTROLLED_TENSION_MAX_BEATS + 1e-9
                        and abs(int(next_note["midi"]) - int(note["midi"])) <= 2
                        and next_chord is not None
                        and next_chord[0] is not None
                        and int(next_note["midi"]) % 12 in next_chord[0]
                    )
                controlled = (
                    resolves_by_step
                    and episode_beats <= CONTROLLED_TENSION_MAX_BEATS + 1e-9
                )
                if controlled:
                    controlled_tension_beats += episode_beats
                    track_controlled += episode_beats
                issue = {
                    "track": track_name,
                    "note": note["name"],
                    "noteStart": rounded_metric(start),
                    "overlapStart": rounded_metric(episode_start),
                    "overlapEnd": rounded_metric(episode_end),
                    "nonChordBeats": rounded_metric(episode_beats),
                    "chord": chord_episode_label(episode_spans),
                    "chordSpans": [
                        {
                            "start": rounded_metric(float(span["start"])),
                            "end": rounded_metric(float(span["end"])),
                            "chord": span["chord"],
                        }
                        for span in episode_spans[:8]
                    ],
                    "chordSpanCount": len(episode_spans),
                    "resolvedByStep": resolves_by_step,
                    "controlledTension": controlled,
                }
                issues.append(issue)
                track_issues.append(issue)
        evaluated = track_fit + track_non_chord
        unresolved_track = max(0.0, track_non_chord - track_controlled)
        track_unresolved_issues = [
            item for item in track_issues if not item["controlledTension"]
        ]
        unresolved_spans = [
            float(item["nonChordBeats"]) for item in track_unresolved_issues
        ]
        longest_unresolved = max(unresolved_spans, default=0.0)
        longest_non_chord = max(
            (float(item["nonChordBeats"]) for item in track_issues), default=0.0
        )
        worst_track_issue = max(
            track_unresolved_issues,
            key=lambda item: float(item["nonChordBeats"]),
            default=None,
        )
        track_fit_rate = (
            1.0 - unresolved_track / evaluated if evaluated > 1e-9 else None
        )
        per_track[track_name] = {
            "fitBeats": rounded_metric(track_fit),
            "nonChordBeats": rounded_metric(track_non_chord),
            "controlledTensionBeats": rounded_metric(track_controlled),
            "unresolvedNonChordBeats": rounded_metric(unresolved_track),
            "longestUnresolvedSpanBeats": rounded_metric(longest_unresolved),
            "longestNonChordEpisodeBeats": rounded_metric(longest_non_chord),
            "ncBeats": rounded_metric(track_nc),
            "evaluatedBeats": rounded_metric(evaluated),
            "fitRate": None if track_fit_rate is None else rounded_metric(track_fit_rate),
        }

        if not notes:
            continue
        if track_fit_rate is None:
            if has_evaluable_chord:
                failure = {
                    "code": "TRACK_HARMONIC_EVIDENCE_MISSING",
                    "track": track_name,
                    "observed": 0.0,
                    "required": "chord-covered duration",
                    "suggestion": "Move this track into chord-covered time or provide a valid chord chart.",
                }
                gate_failures.append(failure)
                warnings.append(
                    f"{track_name} has notes but no duration that can be evaluated against a chord."
                )
            continue

        active_fit_rates.append(track_fit_rate)
        is_pad = track_name in PAD_TRACKS
        fit_floor = PAD_FIT_FLOOR if is_pad else HARMONY_FIT_FLOOR
        full_floor = PAD_FIT_FULL if is_pad else HARMONY_FIT_FULL
        if track_fit_rate + 1e-9 < fit_floor:
            gate_failures.append(
                {
                    "code": "TRACK_HARMONIC_FIT_FAILED",
                    "track": track_name,
                    "observed": rounded_metric(track_fit_rate),
                    "required": fit_floor,
                    "span": (
                        None
                        if worst_track_issue is None
                        else {
                            "start": worst_track_issue["overlapStart"],
                            "end": worst_track_issue["overlapEnd"],
                            "chord": worst_track_issue["chord"],
                        }
                    ),
                    "suggestion": f"Rewrite unresolved chord tones in {track_name}.",
                }
            )
            warnings.append(
                f"{track_name} chord fit is {track_fit_rate:.0%}; required minimum is {fit_floor:.0%}."
            )
            suggestions.append(f"Rewrite unresolved chord tones in {track_name} before balancing other tracks.")
        elif track_fit_rate + 1e-9 < full_floor:
            warnings.append(
                f"{track_name} chord fit is {track_fit_rate:.0%}; {full_floor:.0%} is required for full harmonic credit."
            )
        long_resolved = [
            item
            for item in track_unresolved_issues
            if item["resolvedByStep"]
            and float(item["nonChordBeats"]) > CONTROLLED_TENSION_MAX_BEATS + 1e-9
        ]
        if long_resolved:
            worst_long_resolved = max(
                long_resolved, key=lambda item: float(item["nonChordBeats"])
            )
            warnings.append(
                f"{track_name} has a {float(worst_long_resolved['nonChordBeats']):g}-beat "
                "stepwise-resolved tension; only episodes up to "
                f"{CONTROLLED_TENSION_MAX_BEATS:g} beat receive automatic tension credit."
            )
        if track_fit_rate + 1e-9 < full_floor:
            full_score_eligible = False

    unresolved = max(0.0, non_chord_beats - controlled_tension_beats)
    evaluated_beats = fit_beats + non_chord_beats
    fit_rate = 1.0 - unresolved / evaluated_beats if evaluated_beats > 1e-9 else 0.0
    max_score = int(CATEGORY_SPECS["harmonicFit"]["maxScore"])
    if active_fit_rates:
        balanced_fit = 0.5 * (sum(active_fit_rates) / len(active_fit_rates)) + 0.5 * min(
            active_fit_rates
        )
        score = round(max_score * balanced_fit)
    else:
        balanced_fit = 0.0
        score = 0

    pad_definition = pad_chord_definition_metrics(tracks, spans)
    definition_rate = float(pad_definition["definedChordSpanRate"])
    if float(pad_definition["supportedBeats"]) > 1e-9:
        if definition_rate + 1e-9 < 0.40:
            gate_failures.append(
                {
                    "code": "PAD_CHORD_DEFINITION_FAILED",
                    "track": "VocalPad",
                    "observed": rounded_metric(definition_rate),
                    "required": 0.40,
                    "suggestion": "Use at least two chord tones and include a defining tone in the Pad voicing.",
                }
            )
            warnings.append(
                f"Pad defines the active chord on only {definition_rate:.0%} of supported duration."
            )
            suggestions.append(
                "Use at least two distinct chord tones and include a defining tone instead of root/fifth-only stacks."
            )
        elif definition_rate + 1e-9 < 0.70:
            full_score_eligible = False
            warnings.append(
                f"Pad chord definition is {definition_rate:.0%}; 70% is required for full harmonic credit."
            )

    if gate_failures:
        score = min(score, int(CATEGORY_SPECS["harmonicFit"]["floor"]) - 1)
    elif not full_score_eligible:
        score = min(score, 18)
    uncontrolled_issues = [item for item in issues if not item["controlledTension"]]
    if uncontrolled_issues:
        worst = sorted(
            uncontrolled_issues,
            key=lambda item: (-float(item["nonChordBeats"]), float(item["overlapStart"])),
        )[:12]
        locations = ", ".join(
            f"{item['track']}@{item['overlapStart']:g}-{item['overlapEnd']:g}/{item['chord']}"
            for item in worst
        )
        warnings.append(
            f"Non-exempt harmony/chord mismatch totals {unresolved:g} beats "
            f"({1.0 - fit_rate:.0%} of evaluated duration); worst episodes: {locations}."
        )
        suggestions.append(
            "Review long non-chord episodes at chord boundaries; short stepwise tensions up to "
            f"{CONTROLLED_TENSION_MAX_BEATS:g} beat receive automatic credit."
        )
    worst_episodes = sorted(
        issues,
        key=lambda item: (-float(item["nonChordBeats"]), float(item["overlapStart"])),
    )[:12]
    metrics = {
        "fitBeats": rounded_metric(fit_beats),
        "nonChordBeats": rounded_metric(non_chord_beats),
        "controlledTensionBeats": rounded_metric(controlled_tension_beats),
        "unresolvedNonChordBeats": rounded_metric(unresolved),
        "ncBeats": rounded_metric(nc_beats),
        "evaluatedBeats": rounded_metric(evaluated_beats),
        "fitRate": rounded_metric(fit_rate),
        "balancedFitRate": rounded_metric(balanced_fit),
        "worstParticipatingTrackFitRate": rounded_metric(min(active_fit_rates, default=0.0)),
        "perTrack": per_track,
        "padChordDefinition": pad_definition,
        "gateFailures": gate_failures,
        "worstNonChordEpisodes": worst_episodes,
        "worstNonChordSpans": worst_episodes,
        "totalNonChordSpanCount": sum(
            int(item["chordSpanCount"]) for item in issues
        ),
        "totalNonChordEpisodeCount": len(issues),
    }
    applicable = bool(active_fit_rates or gate_failures)
    return (
        category_result(
            "harmonicFit",
            score,
            applicable=applicable,
            status="scored" if applicable else "notEvaluable",
        ),
        warnings,
        suggestions,
        metrics,
    )


def motion_direction(delta: int) -> int:
    return 0 if delta == 0 else (1 if delta > 0 else -1)


def is_copy_boundary(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> bool:
    """Reset copy evidence only across a genuinely long musical break.

    Melody-aligned BV notes inherit source timing, so short source rests must not
    make an otherwise identical fixed-transposition contour look independent.
    Phrase-shape analysis keeps its finer 0.5-beat boundary separately.
    """
    return float(current["time"]) - note_end(previous) >= COPY_RESET_GAP_BEATS - 1e-9


def exact_duplicate_runs(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
    segments: list[TimeSignatureSegment],
) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    run_start: float | None = None
    run_end = 0.0
    run_notes = 0
    for left_note, right_note in zip(left, right):
        exact = int(left_note["midi"]) == int(right_note["midi"])
        start = float(left_note["time"])
        end = min(note_end(left_note), note_end(right_note))
        contiguous = run_start is not None and start <= run_end + 1e-9
        if exact and (run_start is None or contiguous):
            if run_start is None:
                run_start = start
            run_end = max(run_end, end)
            run_notes += 1
            continue
        if run_start is not None:
            runs.append(
                {
                    "start": rounded_metric(run_start),
                    "end": rounded_metric(run_end),
                    "beats": rounded_metric(run_end - run_start),
                    "notes": run_notes,
                    "bar": bar_key(run_start, segments),
                }
            )
        run_start = start if exact else None
        run_end = end if exact else 0.0
        run_notes = 1 if exact else 0
    if run_start is not None:
        runs.append(
            {
                "start": rounded_metric(run_start),
                "end": rounded_metric(run_end),
                "beats": rounded_metric(run_end - run_start),
                "notes": run_notes,
                "bar": bar_key(run_start, segments),
            }
        )
    return sorted(runs, key=lambda item: (-item["beats"], item["start"]))


def transition_copy_metrics(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
) -> dict[str, Any]:
    count = min(len(left), len(right))
    comparable = 0
    same_direction = 0
    exact_delta = 0
    valid_transitions = 0
    constant_separation = 0
    longest_run = 0
    longest_run_beats = 0.0
    longest_run_start: float | None = None
    longest_run_end: float | None = None
    run_length = 0
    run_start = 0.0
    run_end = 0.0
    same_direction_run = 0
    longest_same_direction_run = 0
    constant_interval_run = 0
    longest_constant_interval_run = 0
    interval_values: set[int] = set()

    for index in range(count):
        interval_values.add(int(left[index]["midi"]) - int(right[index]["midi"]))
        if index == 0:
            continue
        if is_copy_boundary(left[index - 1], left[index]) or is_copy_boundary(
            right[index - 1], right[index]
        ):
            if run_length > longest_run:
                longest_run = run_length
                longest_run_beats = run_end - run_start
                longest_run_start = run_start
                longest_run_end = run_end
            run_length = 0
            same_direction_run = 0
            constant_interval_run = 0
            continue
        valid_transitions += 1
        left_delta = int(left[index]["midi"]) - int(left[index - 1]["midi"])
        right_delta = int(right[index]["midi"]) - int(right[index - 1]["midi"])
        if (
            int(left[index]["midi"]) - int(right[index]["midi"])
            == int(left[index - 1]["midi"]) - int(right[index - 1]["midi"])
        ):
            constant_separation += 1
            constant_interval_run += 1
            longest_constant_interval_run = max(
                longest_constant_interval_run, constant_interval_run
            )
        else:
            constant_interval_run = 0
        both_move = left_delta != 0 and right_delta != 0
        if both_move:
            comparable += 1
            moves_same_direction = motion_direction(left_delta) == motion_direction(right_delta)
            same_direction += moves_same_direction
            if moves_same_direction:
                same_direction_run += 1
                longest_same_direction_run = max(
                    longest_same_direction_run, same_direction_run
                )
            else:
                same_direction_run = 0
        else:
            same_direction_run = 0
        rigid = both_move and left_delta == right_delta
        if rigid:
            exact_delta += 1
            if run_length == 0:
                run_start = min(
                    float(left[index - 1]["time"]), float(right[index - 1]["time"])
                )
            run_length += 1
            run_end = max(note_end(left[index]), note_end(right[index]))
        else:
            if run_length > longest_run:
                longest_run = run_length
                longest_run_beats = run_end - run_start
                longest_run_start = run_start
                longest_run_end = run_end
            run_length = 0
    if run_length > longest_run:
        longest_run = run_length
        longest_run_beats = run_end - run_start
        longest_run_start = run_start
        longest_run_end = run_end

    exact_delta_rate = exact_delta / max(comparable, 1)
    same_direction_rate = same_direction / max(comparable, 1)
    similar_motion_hint = comparable >= 4 and (
        same_direction_rate >= 0.80 and longest_same_direction_run >= 4
    )
    parallel_hint = comparable >= 4 and (
        exact_delta_rate >= 0.45 or longest_run >= 3 or similar_motion_hint
    )
    mechanical_parallel = comparable >= 4 and (
        exact_delta_rate >= 0.75 and longest_run >= 4
    )
    return {
        "validTransitions": valid_transitions,
        "comparableTransitions": comparable,
        "exactDeltaCopyRate": rounded_metric(exact_delta_rate),
        "sameDirectionRate": rounded_metric(same_direction_rate),
        "longestSameDirectionRunTransitions": longest_same_direction_run,
        "constantSeparationRate": rounded_metric(
            constant_separation / max(valid_transitions, 1)
        ),
        "longestConstantIntervalRunTransitions": longest_constant_interval_run,
        "longestRigidRunTransitions": longest_run,
        "longestRigidRunBeats": rounded_metric(longest_run_beats),
        "longestRigidRunStart": (
            None if longest_run_start is None else rounded_metric(longest_run_start)
        ),
        "longestRigidRunEnd": (
            None if longest_run_end is None else rounded_metric(longest_run_end)
        ),
        "intervalVariety": len(interval_values),
        "parallelHint": parallel_hint,
        "similarMotionHint": similar_motion_hint,
        "mechanicalParallel": mechanical_parallel,
    }


def harmony_pair_metrics(
    left_name: str,
    right_name: str,
    tracks: dict[str, list[dict[str, Any]]],
    segments: list[TimeSignatureSegment],
) -> dict[str, Any]:
    left = tracks[left_name]
    right = tracks[right_name]
    count = min(len(left), len(right))
    if count == 0:
        return {
            "pair": f"{left_name}:{right_name}",
            "noteCount": 0,
            "exactMidiDuplicationRate": 0.0,
            "pitchClassDuplicationRate": 0.0,
            "longestExactRunNotes": 0,
            "longestExactRunBeats": 0.0,
            "majorDuplication": False,
            "mechanicalParallel": False,
            "severity": 0.0,
            "worstRuns": [],
            **transition_copy_metrics(left, right),
        }

    exact = 0
    pitch_class = 0
    for left_note, right_note in zip(left, right):
        left_midi = int(left_note["midi"])
        right_midi = int(right_note["midi"])
        exact += left_midi == right_midi
        pitch_class += left_midi % 12 == right_midi % 12

    runs = exact_duplicate_runs(left[:count], right[:count], segments)
    longest_notes = int(runs[0]["notes"]) if runs else 0
    longest_beats = float(runs[0]["beats"]) if runs else 0.0
    exact_rate = exact / count
    pc_rate = pitch_class / count
    functional_rate = max(exact_rate, pc_rate * 0.8)
    continuity_threshold = 8
    continuity = min(1.0, longest_notes / continuity_threshold)
    duplicate_severity = max(0.0, (functional_rate - 0.25) / 0.75) * continuity
    exact_major = (
        functional_rate >= 0.55 and longest_notes >= 4 and longest_beats >= 2
    ) or (
        functional_rate >= 0.35
        and longest_notes >= continuity_threshold
        and longest_beats >= 2
    )
    transition_metrics = transition_copy_metrics(left[:count], right[:count])
    parallel_severity = max(
        float(transition_metrics["exactDeltaCopyRate"]),
        min(1.0, int(transition_metrics["longestRigidRunTransitions"]) / 8),
    )
    severity = max(duplicate_severity, parallel_severity)
    major = exact_major or bool(transition_metrics["mechanicalParallel"])
    return {
        "pair": f"{left_name}:{right_name}",
        "noteCount": count,
        "exactMidiDuplicationRate": rounded_metric(exact_rate),
        "pitchClassDuplicationRate": rounded_metric(pc_rate),
        "longestExactRunNotes": longest_notes,
        "longestExactRunBeats": rounded_metric(longest_beats),
        "majorDuplication": major,
        "severity": rounded_metric(severity),
        "worstRuns": runs[:8],
        **transition_metrics,
    }


def phrase_index_groups(
    notes: list[dict[str, Any]],
    control: list[SectionSpan],
) -> list[list[int]]:
    if not notes:
        return []
    groups = [[0]]
    for index in range(1, len(notes)):
        if is_phrase_boundary(notes[index - 1], notes[index], control):
            groups.append([])
        groups[-1].append(index)
    return groups


def analyze_flat_voice_roles(
    tracks: dict[str, list[dict[str, Any]]],
    lead: list[dict[str, Any]],
    control: list[SectionSpan],
    chord_event_list: list[tuple[float, set[int] | None, str]] | None,
) -> tuple[int, list[str], list[str], dict[str, Any], list[dict[str, Any]]]:
    """Score HHF/LHF as stable lyric-aligned harmony roles.

    These tracks intentionally inherit melody rhythm. Their distinct function
    comes from pitch stability, smooth chord response, and avoiding wholesale
    contour copies of HHA/HHB or LHA/LHB.
    """
    warnings: list[str] = []
    suggestions: list[str] = []
    gate_failures: list[dict[str, Any]] = []
    per_track: dict[str, dict[str, Any]] = {}
    track_scores: list[int] = []

    for track_name in FLAT_TRACKS:
        notes = tracks[track_name]
        count = min(len(notes), len(lead))
        valid_transitions = 0
        motions = 0
        stepwise_motions = 0
        large_leaps = 0
        common_tone_opportunities = 0
        common_tones_retained = 0

        for index in range(1, count):
            previous = notes[index - 1]
            current = notes[index]
            if is_copy_boundary(previous, current) or section_at(
                control, float(previous["time"])
            ) != section_at(control, float(current["time"])):
                continue
            valid_transitions += 1
            delta = int(current["midi"]) - int(previous["midi"])
            if delta:
                motions += 1
                stepwise_motions += abs(delta) <= 2
                large_leaps += abs(delta) > 7

            if chord_event_list:
                previous_chord = active_chord(
                    chord_event_list, float(previous["time"])
                )
                current_chord = active_chord(
                    chord_event_list, float(current["time"])
                )
                if (
                    previous_chord is not None
                    and current_chord is not None
                    and previous_chord[0] is not None
                    and current_chord[0] is not None
                    and previous_chord[0] != current_chord[0]
                    and int(previous["midi"]) % 12
                    in (previous_chord[0] & current_chord[0])
                ):
                    common_tone_opportunities += 1
                    common_tones_retained += int(previous["midi"]) == int(
                        current["midi"]
                    )

        reference_comparisons = []
        for reference_name in FLAT_REFERENCE_TRACKS[track_name]:
            reference_count = min(len(notes), len(tracks[reference_name]))
            reference_comparisons.append(
                {
                    "reference": reference_name,
                    **transition_copy_metrics(
                        notes[:reference_count], tracks[reference_name][:reference_count]
                    ),
                }
            )
        worst_copy = max(
            reference_comparisons,
            key=lambda item: (
                float(item["exactDeltaCopyRate"]),
                int(item["longestRigidRunTransitions"]),
            ),
            default=None,
        )
        maximum_copy_rate = (
            0.0 if worst_copy is None else float(worst_copy["exactDeltaCopyRate"])
        )
        longest_copy_run = (
            0 if worst_copy is None else int(worst_copy["longestRigidRunTransitions"])
        )

        motion_rate = motions / max(valid_transitions, 1)
        stepwise_motion_rate = stepwise_motions / max(motions, 1)
        large_leap_rate = large_leaps / max(valid_transitions, 1)
        common_tone_rate = common_tones_retained / max(common_tone_opportunities, 1)

        if valid_transitions < 2:
            stability_score = 85.0
        elif motion_rate <= 0.55:
            stability_score = 100.0
        elif motion_rate <= 0.85:
            stability_score = 100.0 - (motion_rate - 0.55) / 0.30 * 40.0
        else:
            stability_score = max(35.0, 60.0 - (motion_rate - 0.85) / 0.15 * 25.0)
        smooth_motion_score = 100.0 if motions == 0 else 100.0 * stepwise_motion_rate
        smooth_motion_score = max(0.0, smooth_motion_score - large_leap_rate * 60.0)
        common_tone_score = (
            100.0
            if common_tone_opportunities < 2
            else 100.0 * common_tone_rate
        )
        if maximum_copy_rate <= 0.45:
            role_independence_score = 100.0
        elif maximum_copy_rate < 0.75:
            role_independence_score = 100.0 - (maximum_copy_rate - 0.45) / 0.30 * 40.0
        else:
            role_independence_score = max(
                20.0, 60.0 - (maximum_copy_rate - 0.75) / 0.25 * 40.0
            )
        track_score = round(
            0.35 * stability_score
            + 0.25 * smooth_motion_score
            + 0.25 * common_tone_score
            + 0.15 * role_independence_score
        )
        track_scores.append(track_score)

        if valid_transitions >= 4 and motion_rate > 0.75:
            warnings.append(
                f"{track_name} changes pitch on {motion_rate:.0%} of comparable transitions; "
                "its flat role should retain more common or repeated tones."
            )
            suggestions.append(
                f"Stabilize {track_name} with repeated chord tones and stepwise chord responses."
            )
        if common_tone_opportunities >= 2 and common_tone_rate < 0.50:
            warnings.append(
                f"{track_name} retains only {common_tone_rate:.0%} of available common tones."
            )
            suggestions.append(
                f"Keep more legal common tones in {track_name} across chord changes."
            )
        if (
            worst_copy is not None
            and int(worst_copy["comparableTransitions"]) >= 4
            and maximum_copy_rate >= 0.75
            and longest_copy_run >= 4
        ):
            failure = {
                "code": "FLAT_VOICE_CONTOUR_COPY",
                "track": track_name,
                "reference": worst_copy["reference"],
                "observed": rounded_metric(maximum_copy_rate),
                "required": "stable role distinct from contour harmonies",
                "span": {
                    "start": worst_copy["longestRigidRunStart"],
                    "end": worst_copy["longestRigidRunEnd"],
                },
                "suggestion": (
                    f"Give {track_name} more common-tone retention and less contour copying."
                ),
            }
            gate_failures.append(failure)
            warnings.append(
                f"{track_name} copies {worst_copy['reference']} motion on "
                f"{maximum_copy_rate:.0%} of comparable transitions instead of acting as a stable voice."
            )

        per_track[track_name] = {
            "score": track_score,
            "validTransitions": valid_transitions,
            "motionRate": rounded_metric(motion_rate),
            "stepwiseMotionRate": rounded_metric(stepwise_motion_rate),
            "largeLeapRate": rounded_metric(large_leap_rate),
            "commonToneOpportunities": common_tone_opportunities,
            "commonToneRetentionRate": rounded_metric(common_tone_rate),
            "maximumContourCopyRate": rounded_metric(maximum_copy_rate),
            "longestContourCopyRunTransitions": longest_copy_run,
            "referenceComparisons": reference_comparisons,
        }

    overall_score = round(sum(track_scores) / max(len(track_scores), 1))
    return (
        overall_score,
        warnings,
        suggestions,
        {"score": overall_score, "perTrack": per_track},
        gate_failures,
    )


def longest_close_pitch_run(midis: list[int]) -> int:
    unique = sorted(set(midis))
    if not unique:
        return 0
    longest = 1
    current = 1
    for previous, following in zip(unique, unique[1:]):
        if following - previous <= 2:
            current += 1
            longest = max(longest, current)
        else:
            current = 1
    return longest


def analyze_vertical_voicing(
    tracks: dict[str, list[dict[str, Any]]],
    lead: list[dict[str, Any]],
    segments: list[TimeSignatureSegment],
) -> tuple[int, list[str], list[str], dict[str, Any], list[dict[str, Any]]]:
    """Evaluate the realized seven-part sonority at every melody slot."""
    warnings: list[str] = []
    suggestions: list[str] = []
    gate_failures: list[dict[str, Any]] = []
    count = min(len(lead), *(len(tracks[name]) for name in MELODY_ALIGNED_TRACKS))
    total_beats = 0.0
    collision_beats = 0.0
    dense_cluster_beats = 0.0
    compressed_beats = 0.0
    overloaded_pitch_class_beats = 0.0
    risky_slices: list[dict[str, Any]] = []

    for index in range(count):
        duration = float(lead[index]["duration"])
        total_beats += duration
        voice_midis = {
            "LeadDouble": int(lead[index]["midi"]),
            **{
                track_name: int(tracks[track_name][index]["midi"])
                for track_name in MELODY_ALIGNED_TRACKS
            },
        }
        midis = sorted(voice_midis.values())
        unique_midis = sorted(set(midis))
        span = max(midis) - min(midis)
        close_run = longest_close_pitch_run(midis)
        distinct_pitch_classes = len({midi % 12 for midi in midis})
        collision = len(unique_midis) < len(midis)
        dense_cluster = close_run >= 4
        compressed = span < 14
        overloaded = distinct_pitch_classes >= 6 and compressed
        collision_beats += duration if collision else 0.0
        dense_cluster_beats += duration if dense_cluster else 0.0
        compressed_beats += duration if compressed else 0.0
        overloaded_pitch_class_beats += duration if overloaded else 0.0
        if collision or dense_cluster or overloaded:
            risky_slices.append(
                {
                    "time": rounded_metric(float(lead[index]["time"])),
                    "bar": bar_key(float(lead[index]["time"]), segments),
                    "midis": midis,
                    "spanSemitones": span,
                    "longestClosePitchRun": close_run,
                    "distinctPitchClasses": distinct_pitch_classes,
                    "collision": collision,
                    "denseCluster": dense_cluster,
                }
            )

    collision_rate = collision_beats / max(total_beats, 1e-9)
    dense_cluster_rate = dense_cluster_beats / max(total_beats, 1e-9)
    compressed_rate = compressed_beats / max(total_beats, 1e-9)
    overloaded_rate = overloaded_pitch_class_beats / max(total_beats, 1e-9)
    risk = min(
        100.0,
        10.0 * collision_rate
        + 50.0 * dense_cluster_rate
        + 25.0 * compressed_rate
        + 15.0 * overloaded_rate,
    )
    score = round(100.0 - risk)

    if dense_cluster_rate >= 0.60 and compressed_rate >= 0.60:
        gate_failures.append(
            {
                "code": "VERTICAL_VOICING_CLUSTERED",
                "track": "MelodyAlignedVoices",
                "observed": rounded_metric(dense_cluster_rate),
                "required": "< 0.60 sustained dense-cluster rate",
                "span": (
                    None
                    if not risky_slices
                    else {
                        "start": risky_slices[0]["time"],
                        "bar": risky_slices[0]["bar"],
                    }
                ),
                "suggestion": (
                    "Spread chord functions across registers and remove sustained chains of adjacent seconds."
                ),
            }
        )
    if dense_cluster_rate > 0.25:
        warnings.append(
            f"Melody-aligned voices form four-or-more-note step clusters for "
            f"{dense_cluster_rate:.0%} of evaluated duration."
        )
        suggestions.append(
            "Open the vertical voicing and reserve seconds for brief, intentional color."
        )
    if collision_rate > 0.10:
        warnings.append(
            f"Exact pitch collisions occur for {collision_rate:.0%} of evaluated vertical duration."
        )
    if overloaded_rate > 0.25:
        warnings.append(
            f"Compressed voicings use six or more pitch classes for {overloaded_rate:.0%} "
            "of evaluated duration."
        )

    return (
        score,
        warnings,
        suggestions,
        {
            "score": score,
            "evaluatedBeats": rounded_metric(total_beats),
            "collisionRate": rounded_metric(collision_rate),
            "denseClusterRate": rounded_metric(dense_cluster_rate),
            "compressedVoicingRate": rounded_metric(compressed_rate),
            "overloadedPitchClassRate": rounded_metric(overloaded_rate),
            "worstSlices": risky_slices[:12],
        },
        gate_failures,
    )


def analyze_harmony_shape(
    tracks: dict[str, list[dict[str, Any]]],
    lead: list[dict[str, Any]],
    control: list[SectionSpan],
    segments: list[TimeSignatureSegment],
    chord_event_list: list[tuple[float, set[int] | None, str]] | None = None,
) -> tuple[dict[str, Any], list[str], list[str], dict[str, Any]]:
    warnings: list[str] = []
    suggestions: list[str] = []
    pair_metrics: list[dict[str, Any]] = []
    lead_comparisons: list[dict[str, Any]] = []
    gate_failures: list[dict[str, Any]] = []
    parallel_hint = False
    independence_penalty = 0.0
    bar_voice_counts: dict[str, dict[int, list[int]]] = {}

    for track_name in MELODY_ALIGNED_TRACKS:
        count = min(len(tracks[track_name]), len(lead))
        comparison = {
            "track": track_name,
            **transition_copy_metrics(tracks[track_name][:count], lead[:count]),
        }
        lead_comparisons.append(comparison)
        if comparison["mechanicalParallel"]:
            parallel_hint = True
            independence_penalty += 2.5
            warnings.append(
                f"{track_name} mechanically copies LeadDouble with a fixed interval on "
                f"{float(comparison['exactDeltaCopyRate']):.0%} of comparable transitions; "
                f"longest rigid run is {comparison['longestRigidRunTransitions']} transitions/"
                f"{float(comparison['longestRigidRunBeats']):g} beats. A single parallel line is "
                "a soft risk; rigid multi-voice groups remain delivery failures."
            )
            suggestions.append(
                f"Vary {track_name}'s chord function when the full harmony group also moves in parallel."
            )
        elif comparison["parallelHint"]:
            parallel_hint = True
            warnings.append(
                f"{track_name} follows LeadDouble with exact delta on "
                f"{float(comparison['exactDeltaCopyRate']):.0%} and the same direction on "
                f"{float(comparison['sameDirectionRate']):.0%} of comparable transitions; "
                "short parallel writing is allowed, but this passage cannot receive full independence credit."
            )

    for group_name, group_tracks in HARMONY_GROUPS.items():
        group_pairs: list[dict[str, Any]] = []
        for left_index in range(len(group_tracks)):
            for right_index in range(left_index + 1, len(group_tracks)):
                pair = harmony_pair_metrics(
                    group_tracks[left_index],
                    group_tracks[right_index],
                    tracks,
                    segments,
                )
                pair["group"] = group_name
                pair_metrics.append(pair)
                group_pairs.append(pair)
                if pair["majorDuplication"]:
                    independence_penalty += 12 * float(pair["severity"])
                    failure_code = (
                        "MECHANICAL_PARALLEL_COPY"
                        if pair["mechanicalParallel"]
                        else "HARMONY_EXACT_DUPLICATION"
                    )
                    gate_failures.append(
                        {
                            "code": failure_code,
                            "pair": pair["pair"],
                            "observed": (
                                pair["exactDeltaCopyRate"]
                                if pair["mechanicalParallel"]
                                else pair["exactMidiDuplicationRate"]
                            ),
                            "required": "independent motion and voicing",
                            "span": (
                                {
                                    "start": pair["longestRigidRunStart"],
                                    "end": pair["longestRigidRunEnd"],
                                }
                                if pair["mechanicalParallel"]
                                else (
                                    {
                                        "start": pair["worstRuns"][0]["start"],
                                        "end": pair["worstRuns"][0]["end"],
                                    }
                                    if pair["worstRuns"]
                                    else None
                                )
                            ),
                            "suggestion": f"Give {pair['pair']} distinct chord functions or contrary/oblique motion.",
                        }
                    )
                    if pair["mechanicalParallel"]:
                        warnings.append(
                            f"Harmony pair {pair['pair']} moves as a fixed parallel copy on "
                            f"{float(pair['exactDeltaCopyRate']):.0%} of comparable transitions; "
                            f"longest rigid run is {pair['longestRigidRunTransitions']} transitions."
                        )
                    else:
                        warnings.append(
                            f"Harmony pair {pair['pair']} duplicates for "
                            f"{float(pair['exactMidiDuplicationRate']):.0%} of notes; "
                            f"longest run is {pair['longestExactRunNotes']} notes/"
                            f"{float(pair['longestExactRunBeats']):g} beats."
                        )
                    suggestions.append(
                        f"Give {pair['pair']} distinct chord functions or contrary/oblique motion."
                    )
                elif pair["parallelHint"]:
                    parallel_hint = True
                    warnings.append(
                        f"Harmony pair {pair['pair']} shares exact delta on "
                        f"{float(pair['exactDeltaCopyRate']):.0%} and direction on "
                        f"{float(pair['sameDirectionRate']):.0%} of comparable transitions; "
                        "no severe failure was applied."
                    )
                elif (
                    int(pair["noteCount"]) >= 2
                    and float(pair["exactMidiDuplicationRate"]) >= 0.8
                ):
                    warnings.append(
                        f"{group_tracks[left_index]} and {group_tracks[right_index]} "
                        f"duplicate each other on {float(pair['exactMidiDuplicationRate']):.0%} "
                        "of a short passage; no major deduction was applied."
                    )

        group_rigid_pairs = [
            pair
            for pair in group_pairs
            if int(pair["comparableTransitions"]) >= 4
            and float(pair["exactDeltaCopyRate"]) >= 0.65
            and int(pair["longestRigidRunTransitions"]) >= 4
        ]
        if len(group_rigid_pairs) >= 2:
            gate_failures.append(
                {
                    "code": "MECHANICAL_PARALLEL_GROUP",
                    "group": group_name,
                    "pairs": [pair["pair"] for pair in group_rigid_pairs],
                    "observed": len(group_rigid_pairs),
                    "required": "fewer than 2 rigid pairs",
                    "suggestion": f"Break block motion in the {group_name} group with common tones and contrary motion.",
                }
            )
            warnings.append(
                f"The {group_name} harmony group moves as a rigid block in pairs "
                f"{', '.join(pair['pair'] for pair in group_rigid_pairs)}."
            )

        per_bar: dict[int, list[int]] = {}
        count = min(*(len(tracks[name]) for name in group_tracks), len(lead))
        for index in range(count):
            bar = bar_key(float(lead[index]["time"]), segments)
            independent = len({int(tracks[name][index]["midi"]) for name in group_tracks})
            per_bar.setdefault(bar, []).append(independent)
        bar_voice_counts[group_name] = per_bar

    shape_penalty = 0.0
    contour_metrics: dict[str, dict[str, Any]] = {}
    for track_name in CONTOUR_TRACKS:
        notes = tracks[track_name]
        count = min(len(notes), len(lead))
        groups = phrase_index_groups(notes[:count], control)
        valid_transitions = 0
        stationary_while_melody_moves = 0
        interval_jumps = 0
        large_leaps = 0
        for index in range(1, count):
            if is_phrase_boundary(notes[index - 1], notes[index], control):
                continue
            valid_transitions += 1
            harmony_delta = int(notes[index]["midi"]) - int(notes[index - 1]["midi"])
            melody_delta = int(lead[index]["midi"]) - int(lead[index - 1]["midi"])
            stationary_while_melody_moves += harmony_delta == 0 and melody_delta != 0
            previous_interval = int(notes[index - 1]["midi"]) - int(lead[index - 1]["midi"])
            current_interval = int(notes[index]["midi"]) - int(lead[index]["midi"])
            interval_jumps += abs(current_interval - previous_interval) > 5
            large_leaps += abs(harmony_delta) > 7

        template_notes = 0
        for group in groups:
            if len(group) < 4:
                continue
            harmony_unique = len({int(notes[index]["midi"]) for index in group})
            melody_unique = len({int(lead[index]["midi"]) for index in group})
            if harmony_unique <= 2 and melody_unique >= 3:
                template_notes += len(group)
        extreme = sum(
            abs(int(note["midi"]) - int(lead_note["midi"])) > 12
            for note, lead_note in zip(notes[:count], lead[:count])
        )
        stationary_rate = stationary_while_melody_moves / max(valid_transitions, 1)
        template_rate = template_notes / max(count, 1)
        interval_jump_rate = interval_jumps / max(valid_transitions, 1)
        large_leap_rate = large_leaps / max(valid_transitions, 1)
        extreme_rate = extreme / max(count, 1)
        risk = (
            max(0.0, (stationary_rate - 0.4) / 0.6) * 2.5
            + template_rate * 2.5
            + interval_jump_rate * 2.0
            + large_leap_rate * 2.0
            + extreme_rate * 2.5
        )
        shape_penalty += min(5.0, risk)
        contour_metrics[track_name] = {
            "phraseCount": len(groups),
            "stationaryWhileMelodyMovesRate": rounded_metric(stationary_rate),
            "twoNoteTemplateRate": rounded_metric(template_rate),
            "relativeIntervalJumpRate": rounded_metric(interval_jump_rate),
            "largeLeapRate": rounded_metric(large_leap_rate),
            "extremeSpacingRate": rounded_metric(extreme_rate),
        }
        if risk >= 2.0:
            risk_details = []
            for label, rate in (
                ("stationary", stationary_rate),
                ("two-note template", template_rate),
                ("relative-interval jumps", interval_jump_rate),
                ("large leaps", large_leap_rate),
                ("extreme spacing", extreme_rate),
            ):
                if rate > 0:
                    risk_details.append(f"{label}={rate:.0%}")
            warnings.append(
                f"{track_name} has phrase-level contour risk: "
                f"{', '.join(risk_details) or 'combined threshold exceeded'}."
            )
            suggestions.append(
                f"Revise {track_name} across phrases with independent contour and controlled interval changes."
            )
        if large_leaps:
            warnings.append(
                f"{track_name} has {large_leaps} leaps larger than a fifth away from phrase boundaries."
            )
            suggestions.append(f"Smooth non-boundary large jumps in {track_name}.")

    worst_bars: dict[str, list[dict[str, Any]]] = {}
    for group_name, per_bar in bar_voice_counts.items():
        rows = [
            {
                "bar": bar,
                "averageIndependentVoices": rounded_metric(sum(values) / len(values)),
                "minimumIndependentVoices": min(values),
            }
            for bar, values in per_bar.items()
        ]
        worst_bars[group_name] = sorted(
            rows, key=lambda item: (item["averageIndependentVoices"], item["bar"])
        )[:8]

    (
        flat_role_score,
        flat_warnings,
        flat_suggestions,
        flat_role_metrics,
        flat_gate_failures,
    ) = analyze_flat_voice_roles(
        tracks, lead, control, chord_event_list
    )
    (
        vertical_voicing_score,
        vertical_warnings,
        vertical_suggestions,
        vertical_voicing_metrics,
        vertical_gate_failures,
    ) = analyze_vertical_voicing(tracks, lead, segments)
    warnings.extend(flat_warnings)
    warnings.extend(vertical_warnings)
    suggestions.extend(flat_suggestions)
    suggestions.extend(vertical_suggestions)
    gate_failures.extend(flat_gate_failures)
    gate_failures.extend(vertical_gate_failures)

    max_score = int(CATEGORY_SPECS["harmonyShape"]["maxScore"])
    legacy_deductions = min(max_score, math.ceil(independence_penalty + shape_penalty))
    legacy_score = max_score - legacy_deductions
    core_points = round(17 * legacy_score / max_score)
    flat_role_points = round(4 * flat_role_score / 100)
    vertical_voicing_points = round(4 * vertical_voicing_score / 100)
    score = core_points + flat_role_points + vertical_voicing_points
    available_transition_evidence = max(
        (int(item["validTransitions"]) for item in lead_comparisons),
        default=0,
    )
    limited_evidence = available_transition_evidence < 4
    if gate_failures:
        score = min(score, int(CATEGORY_SPECS["harmonyShape"]["floor"]) - 1)
    elif parallel_hint or limited_evidence:
        score = min(score, 22)
    if limited_evidence:
        warnings.append(
            "Harmony independence has only "
            f"{available_transition_evidence} copy-analysis transitions; full independence credit is unavailable."
        )
        suggestions.append(
            "Review harmony independence manually when the source contains fewer than four comparable within-phrase transitions."
        )
    metrics = {
        "pairs": sorted(pair_metrics, key=lambda item: -float(item["severity"])),
        "leadComparisons": lead_comparisons,
        "worstIndependentVoiceBars": worst_bars,
        "contours": contour_metrics,
        "flatVoiceRoles": flat_role_metrics,
        "verticalVoicing": vertical_voicing_metrics,
        "componentScores": {
            "coreContourAndIndependence": {"score": core_points, "maxScore": 17},
            "flatVoiceRoles": {"score": flat_role_points, "maxScore": 4},
            "verticalVoicing": {"score": vertical_voicing_points, "maxScore": 4},
        },
        "limitedEvidence": limited_evidence,
        "availableTransitionEvidence": available_transition_evidence,
        "gateFailures": gate_failures,
        "independencePenalty": rounded_metric(independence_penalty),
        "shapePenalty": rounded_metric(shape_penalty),
        "legacyDeductions": legacy_deductions,
        "deductions": max_score - score,
    }
    return (
        category_result("harmonyShape", score),
        warnings,
        suggestions,
        metrics,
    )


def is_phrase_boundary(
    previous: dict[str, Any],
    current: dict[str, Any],
    control: list[SectionSpan],
) -> bool:
    previous_end = note_end(previous)
    current_time = float(current["time"])
    if current_time - previous_end >= 0.5 - 1e-9:
        return True
    if section_at(control, float(previous["time"])) != section_at(control, current_time):
        return True
    return False


def pad_chord_response_metrics(
    tracks: dict[str, list[dict[str, Any]]],
    chord_event_list: list[tuple[float, set[int] | None, str]],
) -> dict[str, Any]:
    note_events: list[tuple[float, int, str, dict[str, Any]]] = []
    for track_name in PAD_TRACKS:
        for note in tracks[track_name]:
            note_events.append((float(note["time"]), 1, track_name, note))
            note_events.append((note_end(note), 0, track_name, note))
    note_events.sort(key=lambda item: (item[0], item[1], item[2]))

    active: dict[str, dict[str, Any]] = {}
    event_index = 0
    evaluated = 0
    responses = 0
    longest_unchanged_run = 0
    unchanged_run = 0
    boundaries: list[dict[str, Any]] = []

    for chord_index in range(1, len(chord_event_list)):
        boundary, current_pcs, current_name = chord_event_list[chord_index]
        _, previous_pcs, previous_name = chord_event_list[chord_index - 1]
        while event_index < len(note_events) and note_events[event_index][0] < boundary - 1e-9:
            _, kind, track_name, note = note_events[event_index]
            if kind == 0:
                if active.get(track_name) is note:
                    active.pop(track_name, None)
            else:
                active[track_name] = note
            event_index += 1
        before = dict(active)
        while event_index < len(note_events) and abs(note_events[event_index][0] - boundary) <= 1e-9:
            _, kind, track_name, note = note_events[event_index]
            if kind == 0:
                if active.get(track_name) is note:
                    active.pop(track_name, None)
            else:
                active[track_name] = note
            event_index += 1
        after = dict(active)

        if (
            previous_pcs is None
            or current_pcs is None
            or previous_pcs == current_pcs
            or len(before) < 3
            or len(after) < 3
        ):
            unchanged_run = 0
            continue
        evaluated += 1
        before_midis = sorted(int(note["midi"]) for note in before.values())
        after_midis = sorted(int(note["midi"]) for note in after.values())
        voicing_changed = before_midis != after_midis
        still_fits = all(int(note["midi"]) % 12 in current_pcs for note in after.values())
        responded = voicing_changed or still_fits
        responses += responded
        if not voicing_changed:
            unchanged_run += 1
            longest_unchanged_run = max(longest_unchanged_run, unchanged_run)
        else:
            unchanged_run = 0
        if len(boundaries) < 12:
            boundaries.append(
                {
                    "time": rounded_metric(float(boundary)),
                    "fromChord": previous_name,
                    "toChord": current_name,
                    "voicingChanged": voicing_changed,
                    "stillFitsAfterBoundary": still_fits,
                }
            )

    return {
        "evaluatedChordTransitions": evaluated,
        "harmonicChangeResponses": responses,
        "harmonicChangeResponseRate": rounded_metric(responses / max(evaluated, 1)),
        "longestUnchangedVoicingRun": longest_unchanged_run,
        "chordTransitionDetails": boundaries,
    }


def pad_timeline_metrics(
    tracks: dict[str, list[dict[str, Any]]],
    duration: float,
    control: list[SectionSpan],
    melody: list[dict[str, Any]],
    chord_event_list: list[tuple[float, set[int] | None, str]],
    segments: list[TimeSignatureSegment],
) -> dict[str, Any]:
    pad_events: dict[float, list[tuple[int, str, dict[str, Any]]]] = {}
    melody_events: dict[float, list[tuple[int, int]]] = {}
    boundaries = {0.0, duration}
    entry_times: set[float] = set()
    total_note_duration = 0.0
    track_entry_counts: dict[str, int] = {}
    notes_by_bar: dict[int, int] = {}
    span_values = [
        {
            "start": span.start,
            "end": span.end,
            "section": span.section,
            "totalBeats": 0.0,
            "atLeastOneBeats": 0.0,
            "atLeastThreeBeats": 0.0,
            "voiceBeats": 0.0,
            "firstHalfEffectiveBeats": 0.0,
            "secondHalfEffectiveBeats": 0.0,
        }
        for span in control
    ]

    for track_name in PAD_TRACKS:
        notes = tracks[track_name]
        track_entry_counts[track_name] = len(notes)
        for note in notes:
            start = max(0.0, float(note["time"]))
            end = min(duration, note_end(note))
            if end <= start:
                continue
            boundaries.update((start, end))
            pad_events.setdefault(start, []).append((1, track_name, note))
            pad_events.setdefault(end, []).append((0, track_name, note))
            entry_times.add(start)
            total_note_duration += end - start
            bar = bar_key(start, segments)
            notes_by_bar[bar] = notes_by_bar.get(bar, 0) + 1

    for index, note in enumerate(melody):
        start = max(0.0, float(note["time"]))
        end = min(duration, note_end(note))
        if end <= start:
            continue
        boundaries.update((start, end))
        melody_events.setdefault(start, []).append((1, index))
        melody_events.setdefault(end, []).append((0, index))

    for span in control:
        boundaries.add(max(0.0, min(duration, span.start)))
        boundaries.add(max(0.0, min(duration, span.end)))
    for event_time, _, _ in chord_event_list:
        boundaries.add(max(0.0, min(duration, float(event_time))))

    section_metrics: dict[str, dict[str, float]] = {}
    track_coverage = {track_name: 0.0 for track_name in PAD_TRACKS}
    active_pad: dict[str, dict[str, Any]] = {}
    active_melody: set[int] = set()
    active_beats = 0.0
    fewer_than_three_beats = 0.0
    crossing_beats = 0.0
    wide_spacing_beats = 0.0
    melody_active_beats = 0.0
    pad_during_melody_beats = 0.0
    no_bv_pad_beats = 0.0
    ordered_boundaries = sorted(boundaries)
    control_cursor = 0
    chord_cursor = 0
    eligible_chord_spans: dict[str, set[int]] = {}
    supported_chord_spans: dict[str, set[int]] = {}

    for index, time in enumerate(ordered_boundaries[:-1]):
        for kind, track_name, note in sorted(
            pad_events.get(time, []), key=lambda item: (item[0], item[1])
        ):
            if kind == 0:
                if active_pad.get(track_name) is note:
                    active_pad.pop(track_name, None)
            else:
                active_pad[track_name] = note
        for kind, melody_index in sorted(melody_events.get(time, [])):
            if kind == 0:
                active_melody.discard(melody_index)
            else:
                active_melody.add(melody_index)

        next_time = ordered_boundaries[index + 1]
        span_duration = next_time - time
        if span_duration <= 1e-9:
            continue
        midpoint = time + span_duration / 2
        while control_cursor < len(control) and control[control_cursor].end <= midpoint:
            control_cursor += 1
        active_control_index = None
        if (
            control_cursor < len(control)
            and control[control_cursor].start <= midpoint < control[control_cursor].end
        ):
            active_control_index = control_cursor
        section = control[active_control_index].section if active_control_index is not None else "default"
        active_count = len(active_pad)
        while (
            chord_cursor + 1 < len(chord_event_list)
            and float(chord_event_list[chord_cursor + 1][0]) <= midpoint + 1e-9
        ):
            chord_cursor += 1
        if chord_event_list and section != "no_bv":
            chord_pcs = chord_event_list[chord_cursor][1]
            if chord_pcs is not None:
                eligible_chord_spans.setdefault(section, set()).add(chord_cursor)
                if active_count >= 3:
                    supported_chord_spans.setdefault(section, set()).add(chord_cursor)
        metrics = section_metrics.setdefault(
            section,
            {
                "totalBeats": 0.0,
                "atLeastOneBeats": 0.0,
                "atLeastThreeBeats": 0.0,
                "fourVoiceBeats": 0.0,
            },
        )
        metrics["totalBeats"] += span_duration
        if active_count:
            metrics["atLeastOneBeats"] += span_duration
        if active_count >= 3:
            metrics["atLeastThreeBeats"] += span_duration
        if active_count == 4:
            metrics["fourVoiceBeats"] += span_duration

        if active_control_index is not None:
            span_metric = span_values[active_control_index]
            span_metric["totalBeats"] += span_duration
            span_metric["voiceBeats"] += active_count * span_duration
            effective_beats = (0.25 if active_count >= 1 else 0.0) + (
                0.75 if active_count >= 3 else 0.0
            )
            effective_beats *= span_duration
            if active_count:
                span_metric["atLeastOneBeats"] += span_duration
            if active_count >= 3:
                span_metric["atLeastThreeBeats"] += span_duration
            span_midpoint = (float(span_metric["start"]) + float(span_metric["end"])) / 2
            half_key = (
                "firstHalfEffectiveBeats"
                if midpoint < span_midpoint
                else "secondHalfEffectiveBeats"
            )
            span_metric[half_key] += effective_beats

        if section == "no_bv":
            if active_count:
                no_bv_pad_beats += span_duration
            continue

        if active_count:
            active_beats += span_duration
            if active_count < 3:
                fewer_than_three_beats += span_duration
        for track_name in active_pad:
            track_coverage[track_name] += span_duration
        if active_melody:
            melody_active_beats += span_duration
            if active_count:
                pad_during_melody_beats += span_duration

        active_order = [track_name for track_name in PAD_TRACKS if track_name in active_pad]
        if any(
            int(active_pad[upper]["midi"]) <= int(active_pad[lower]["midi"])
            for upper, lower in zip(active_order, active_order[1:])
        ):
            crossing_beats += span_duration
        if any(
            int(active_pad[upper]["midi"]) - int(active_pad[lower]["midi"])
            > (19 if (upper, lower) == ("VocalPad3", "VocalPad4") else 12)
            for upper, lower in zip(active_order, active_order[1:])
        ):
            wide_spacing_beats += span_duration

    eligible_beats = sum(
        metrics["totalBeats"]
        for section, metrics in section_metrics.items()
        if section != "no_bv"
    )
    normalized_sections: dict[str, dict[str, float]] = {}
    for section, values in sorted(section_metrics.items()):
        total = values["totalBeats"]
        at_least_one_rate = values["atLeastOneBeats"] / max(total, 1e-9)
        at_least_three_rate = values["atLeastThreeBeats"] / max(total, 1e-9)
        normalized_sections[section] = {
            "totalBeats": rounded_metric(total),
            "atLeastOneBeats": rounded_metric(values["atLeastOneBeats"]),
            "atLeastThreeBeats": rounded_metric(values["atLeastThreeBeats"]),
            "fourVoiceBeats": rounded_metric(values["fourVoiceBeats"]),
            "atLeastOneRate": rounded_metric(at_least_one_rate),
            "atLeastThreeRate": rounded_metric(at_least_three_rate),
            "fourVoiceRate": rounded_metric(values["fourVoiceBeats"] / max(total, 1e-9)),
            "eligibleChordSpans": len(eligible_chord_spans.get(section, set())),
            "supportedChordSpans": len(supported_chord_spans.get(section, set())),
            "effectiveCoverageRate": rounded_metric(
                0.25 * at_least_one_rate + 0.75 * at_least_three_rate
            ),
        }

    chord_times = {round(float(event[0]), 9) for event in chord_event_list}
    entries_on_chords = sum(1 for time in entry_times if round(time, 9) in chord_times)
    structural_boundaries = {float(event[0]) for event in chord_event_list} | {
        float(span.start) for span in control
    } | {float(span.end) for span in control}
    previous_melody: dict[str, Any] | None = None
    for note in melody:
        if previous_melody is None or is_phrase_boundary(previous_melody, note, control):
            structural_boundaries.add(float(note["time"]))
        previous_melody = note
    ordered_structural_boundaries = sorted(structural_boundaries)
    entries_near_boundaries = 0
    for entry_time in entry_times:
        boundary_index = bisect_left(ordered_structural_boundaries, entry_time)
        neighbors = ordered_structural_boundaries[
            max(0, boundary_index - 1) : boundary_index + 1
        ]
        if any(abs(entry_time - boundary) <= 0.25 + 1e-9 for boundary in neighbors):
            entries_near_boundaries += 1
    ordered_entries = sorted(entry_times)
    isolated_short_entries: list[float] = []
    for index, entry_time in enumerate(ordered_entries):
        starts = [
            note
            for kind, _, note in pad_events.get(entry_time, [])
            if kind == 1
        ]
        longest = max((float(note["duration"]) for note in starts), default=0.0)
        previous_gap = float("inf") if index == 0 else entry_time - ordered_entries[index - 1]
        next_gap = (
            float("inf")
            if index + 1 == len(ordered_entries)
            else ordered_entries[index + 1] - entry_time
        )
        if longest <= 0.5 + 1e-9 and previous_gap >= 2 and next_gap >= 2:
            isolated_short_entries.append(entry_time)
    normalized_spans = []
    for values in span_values:
        total = float(values["totalBeats"])
        first_total = total / 2
        second_total = total - first_total
        normalized_spans.append(
            {
                "start": rounded_metric(float(values["start"])),
                "end": rounded_metric(float(values["end"])),
                "section": values["section"],
                "effectiveCoverageRate": rounded_metric(
                    (
                        0.25 * float(values["atLeastOneBeats"])
                        + 0.75 * float(values["atLeastThreeBeats"])
                    )
                    / max(total, 1e-9)
                ),
                "averageActiveVoices": rounded_metric(
                    float(values["voiceBeats"]) / max(total, 1e-9)
                ),
                "firstHalfEffectiveCoverageRate": rounded_metric(
                    float(values["firstHalfEffectiveBeats"]) / max(first_total, 1e-9)
                ),
                "secondHalfEffectiveCoverageRate": rounded_metric(
                    float(values["secondHalfEffectiveBeats"]) / max(second_total, 1e-9)
                ),
            }
        )
    effective_pad_beats = max(0.0, active_beats - fewer_than_three_beats)
    response_metrics = pad_chord_response_metrics(tracks, chord_event_list)
    return {
        "eligibleBeats": rounded_metric(eligible_beats),
        "effectivePadBeats": rounded_metric(effective_pad_beats),
        "activeCompletenessRate": rounded_metric(
            effective_pad_beats / max(active_beats, 1e-9)
        ),
        "sections": normalized_sections,
        "trackCoverageBeats": {
            track_name: rounded_metric(beats)
            for track_name, beats in track_coverage.items()
        },
        "trackCoverageRates": {
            track_name: rounded_metric(beats / max(eligible_beats, 1e-9))
            for track_name, beats in track_coverage.items()
        },
        "activeBeats": rounded_metric(active_beats),
        "fewerThanThreeBeats": rounded_metric(fewer_than_three_beats),
        "crossingBeats": rounded_metric(crossing_beats),
        "wideSpacingBeats": rounded_metric(wide_spacing_beats),
        "noBvPadBeats": rounded_metric(no_bv_pad_beats),
        "melodyActiveBeats": rounded_metric(melody_active_beats),
        "padDuringMelodyBeats": rounded_metric(pad_during_melody_beats),
        "padDuringMelodyRate": rounded_metric(
            pad_during_melody_beats / max(melody_active_beats, 1e-9)
        ),
        "entryCount": len(entry_times),
        "trackEntryCounts": track_entry_counts,
        "averageNoteDuration": rounded_metric(
            total_note_duration / max(sum(track_entry_counts.values()), 1)
        ),
        "entriesOnChordEvents": entries_on_chords,
        "entriesOnChordEventRate": rounded_metric(
            entries_on_chords / max(len(entry_times), 1)
        ),
        "entriesNearStructuralBoundaries": entries_near_boundaries,
        "entriesNearStructuralBoundaryRate": rounded_metric(
            entries_near_boundaries / max(len(entry_times), 1)
        ),
        "isolatedShortEntries": [rounded_metric(time) for time in isolated_short_entries[:12]],
        "isolatedShortEntryCount": len(isolated_short_entries),
        "sectionSpans": normalized_spans,
        "notesByBar": {str(bar): count for bar, count in sorted(notes_by_bar.items())},
        **response_metrics,
    }


def pad_target(
    section: str,
    pad_policy: str,
    has_control: bool,
) -> tuple[float, float]:
    low, high = PAD_SECTION_TARGETS.get(section, PAD_SECTION_TARGETS["default"])
    if section == "default" and has_control:
        low, high = 0.0, 0.50
    if pad_policy == "required":
        low = max(low, PAD_REQUIRED_MINIMUMS.get(section, PAD_REQUIRED_MINIMUMS["default"]))
    return low, high


def analyze_section_development(
    coverage: dict[str, Any],
) -> tuple[float, list[str], list[str], dict[str, Any]]:
    warnings: list[str] = []
    suggestions: list[str] = []
    penalty = 0.0
    spans = coverage.get("sectionSpans", [])
    chorus_comparisons: list[dict[str, Any]] = []
    pre_chorus_builds: list[dict[str, Any]] = []
    bridge_comparisons: list[dict[str, Any]] = []

    previous = None
    for span in spans:
        current_rate = float(span["effectiveCoverageRate"])
        if span["section"] == "chorus" and previous is not None:
            previous_rate = float(previous["effectiveCoverageRate"])
            delta = current_rate - previous_rate
            row = {
                "start": span["start"],
                "previousSection": previous["section"],
                "previousCoverageRate": rounded_metric(previous_rate),
                "chorusCoverageRate": rounded_metric(current_rate),
                "coverageDelta": rounded_metric(delta),
            }
            chorus_comparisons.append(row)
            if delta < 0.1:
                penalty += min(2.0, max(0.0, (0.1 - delta) / 0.1 * 2.0))
                warnings.append(
                    f"Chorus at {float(span['start']):g} has only {delta:+.0%} Pad coverage "
                    f"change from preceding {previous['section']}."
                )
                suggestions.append(
                    "Create a perceptible chorus lift through Pad activity or active voice count."
                )
        elif span["section"] == "pre_chorus":
            first = float(span["firstHalfEffectiveCoverageRate"])
            second = float(span["secondHalfEffectiveCoverageRate"])
            delta = second - first
            pre_chorus_builds.append(
                {
                    "start": span["start"],
                    "firstHalfRate": rounded_metric(first),
                    "secondHalfRate": rounded_metric(second),
                    "buildDelta": rounded_metric(delta),
                }
            )
            if current_rate > 0.05 and delta < 0.05:
                penalty += min(1.5, max(0.0, (0.05 - delta) / 0.05 * 1.5))
                warnings.append(
                    f"Active pre_chorus at {float(span['start']):g} is static; "
                    f"second-half Pad change is {delta:+.0%}."
                )
                suggestions.append("Shape active pre-chorus Pad as a build, entry, or directional change.")
        elif span["section"] == "bridge" and previous is not None:
            previous_rate = float(previous["effectiveCoverageRate"])
            contrast = abs(current_rate - previous_rate)
            bridge_comparisons.append(
                {
                    "start": span["start"],
                    "previousSection": previous["section"],
                    "coverageContrast": rounded_metric(contrast),
                }
            )
            if max(current_rate, previous_rate) > 0.05 and contrast < 0.05:
                penalty += 1.0
                warnings.append(
                    f"Bridge at {float(span['start']):g} has little Pad contrast from "
                    f"preceding {previous['section']}."
                )
        if span["section"] != "no_bv":
            previous = span

    entry_count = int(coverage.get("entryCount", 0))
    boundary_rate = float(coverage.get("entriesNearStructuralBoundaryRate", 0.0))
    isolated_count = int(coverage.get("isolatedShortEntryCount", 0))
    if entry_count >= 3 and boundary_rate < 0.4:
        penalty += (0.4 - boundary_rate) / 0.4
        warnings.append(
            f"Only {boundary_rate:.0%} of Pad entries are near chord, phrase, or section boundaries."
        )
        suggestions.append("Align Pad entries/exits with musical boundaries or sustain through context.")
    if isolated_count:
        isolated_rate = isolated_count / max(entry_count, 1)
        penalty += min(2.0, isolated_rate * 4)
        warnings.append(
            f"Found {isolated_count} isolated short Pad chord hits; first positions are "
            f"{coverage.get('isolatedShortEntries', [])[:8]}."
        )
        suggestions.append("Replace isolated short Pad hits with contextual entries or omit them.")

    return (
        penalty,
        warnings,
        suggestions,
        {
            "chorusComparisons": chorus_comparisons,
            "preChorusBuilds": pre_chorus_builds,
            "bridgeComparisons": bridge_comparisons,
            "entryBoundaryRate": rounded_metric(boundary_rate),
            "isolatedShortEntryCount": isolated_count,
            "penalty": rounded_metric(penalty),
        },
    )


def target_band_score(rate: float, low: float, high: float) -> float:
    if rate < low:
        return rate / max(low, 1e-9)
    if rate <= high:
        return 1.0
    upper_span = max(1e-9, 1.0 - high)
    return max(0.5, 1.0 - 0.5 * (rate - high) / upper_span)


def category_result(
    name: str,
    score: int,
    *,
    floor: int | None = None,
    applicable: bool = True,
    status: str = "scored",
) -> dict[str, Any]:
    spec = CATEGORY_SPECS[name]
    max_score = int(spec["maxScore"])
    resolved_floor = int(spec["floor"] if floor is None else floor)
    resolved_score = max(0, min(max_score, int(score)))
    return {
        "score": resolved_score,
        "maxScore": max_score,
        "floor": resolved_floor,
        "applicable": applicable,
        "status": status,
        "passedFloor": not applicable or resolved_score >= resolved_floor,
    }


def score_pad_presence(
    coverage: dict[str, Any],
    pad_policy: str,
    has_control: bool,
) -> tuple[dict[str, Any], list[str], list[str], dict[str, Any]]:
    warnings: list[str] = []
    suggestions: list[str] = []
    has_pad = int(coverage["entryCount"]) > 0
    no_bv_beats = float(coverage["noBvPadBeats"])
    details: dict[str, Any] = {
        "policy": pad_policy,
        "sectionPolicySource": "control" if has_control else "default",
        "requiredSectionFailures": [],
        "gateFailures": [],
    }

    if no_bv_beats > 0:
        warnings.append(f"Pad texture sounds inside no_bv for {no_bv_beats:g} beats.")
        suggestions.append("End pad notes before no_bv spans; this is also a hard contract violation.")

    if pad_policy == "optional" and not has_pad:
        details["statusReason"] = "Pad was explicitly omitted by optional policy."
        details["evidenceSufficient"] = True
        return (
            category_result(
                "padPresence", 0, floor=0, applicable=False, status="omitted"
            ),
            warnings,
            suggestions,
            details,
        )

    section_scores: dict[str, dict[str, Any]] = {}
    weighted_score = 0.0
    weighted_beats = 0.0
    required_failures: list[str] = []
    chord_support_failures: list[str] = []
    for section, metrics in coverage["sections"].items():
        if section == "no_bv":
            continue
        total_beats = float(metrics["totalBeats"])
        rate = float(metrics["effectiveCoverageRate"])
        low, high = pad_target(section, pad_policy, has_control)
        band_score = target_band_score(rate, low, high)
        section_scores[section] = {
            "targetMin": low,
            "targetMax": high,
            "bandScore": rounded_metric(band_score),
        }
        weighted_score += band_score * total_beats
        weighted_beats += total_beats
        if low > 0 and rate + 1e-9 < low:
            required_failures.append(section)
            warnings.append(
                f"Pad effective coverage in {section} is {rate:.0%}; target minimum is {low:.0%}."
            )
            suggestions.append(f"Add useful three- or four-part pad support in {section}.")
        eligible_chords = int(metrics.get("eligibleChordSpans", 0))
        supported_chords = int(metrics.get("supportedChordSpans", 0))
        if low > 0 and eligible_chords >= 4 and supported_chords < 2:
            chord_support_failures.append(section)
            warnings.append(
                f"Pad supports only {supported_chords} of {eligible_chords} chord spans in {section}; "
                "at least 2 are required for writing evidence."
            )

    if weighted_beats <= 1e-9:
        details.update({"sectionTargets": section_scores, "statusReason": "No eligible pad time."})
        return (
            category_result(
                "padPresence", 0, floor=0, applicable=False, status="notEvaluable"
            ),
            warnings,
            suggestions,
            details,
        )

    max_score = int(CATEGORY_SPECS["padPresence"]["maxScore"])
    score = round(max_score * weighted_score / weighted_beats)
    gate_failures: list[dict[str, Any]] = []
    for section in required_failures:
        observed = float(coverage["sections"][section]["effectiveCoverageRate"])
        required, _ = pad_target(section, pad_policy, has_control)
        gate_failures.append(
            {
                "code": "PAD_EVIDENCE_INSUFFICIENT",
                "section": section,
                "observed": rounded_metric(observed),
                "required": required,
                "suggestion": f"Add coordinated three- or four-part Pad support in {section}.",
            }
        )
    active_completeness = float(coverage.get("activeCompletenessRate", 0.0))
    if has_pad and active_completeness + 1e-9 < PAD_ACTIVE_COMPLETENESS_FLOOR:
        gate_failures.append(
            {
                "code": "PAD_ACTIVE_COMPLETENESS_FAILED",
                "track": "VocalPad",
                "observed": rounded_metric(active_completeness),
                "required": PAD_ACTIVE_COMPLETENESS_FLOOR,
                "suggestion": "Use at least three coordinated Pad voices for most active Pad time.",
            }
        )
        warnings.append(
            f"Pad has at least three active voices for only {active_completeness:.0%} of active Pad time; "
            f"required minimum is {PAD_ACTIVE_COMPLETENESS_FLOOR:.0%}."
        )
    if has_pad and float(coverage.get("effectivePadBeats", 0.0)) <= 1e-9:
        gate_failures.append(
            {
                "code": "PAD_EVIDENCE_INSUFFICIENT",
                "track": "VocalPad",
                "observed": 0.0,
                "required": "three-voice effective duration",
                "suggestion": "Replace isolated Pad notes with a sustained three- or four-part texture.",
            }
        )
    for section in chord_support_failures:
        gate_failures.append(
            {
                "code": "PAD_CHORD_SUPPORT_INSUFFICIENT",
                "section": section,
                "observed": int(coverage["sections"][section].get("supportedChordSpans", 0)),
                "required": 2,
                "suggestion": f"Support at least two chord spans with a complete Pad texture in {section}.",
            }
        )
    if pad_policy == "optional" and has_pad and gate_failures:
        gate_failures.append(
            {
                "code": "PARTIAL_OPTIONAL_PAD",
                "track": "VocalPad",
                "observed": int(coverage.get("entryCount", 0)),
                "required": "omit all Pad notes or meet sectional evidence",
                "suggestion": "Remove all optional Pad fragments or complete them to sectional evidence quality.",
            }
        )
    if gate_failures:
        score = min(score, int(CATEGORY_SPECS["padPresence"]["floor"]) - 1)
    if no_bv_beats > 0:
        score = max(0, score - min(5, math.ceil(no_bv_beats)))
    floor = int(CATEGORY_SPECS["padPresence"]["floor"])
    details.update(
        {
            "sectionTargets": section_scores,
            "requiredSectionFailures": required_failures,
            "chordSupportFailures": chord_support_failures,
            "weightedBandScore": rounded_metric(weighted_score / weighted_beats),
            "evidenceSufficient": not gate_failures,
            "gateFailures": gate_failures,
        }
    )
    return (
        category_result("padPresence", score, floor=floor),
        warnings,
        suggestions,
        details,
    )


def pad_snapshots(
    tracks: dict[str, list[dict[str, Any]]],
) -> list[tuple[float, dict[str, dict[str, Any]]]]:
    events: list[tuple[float, int, str, dict[str, Any]]] = []
    for track_name in PAD_TRACKS:
        for note in tracks[track_name]:
            events.append((float(note["time"]), 1, track_name, note))
            events.append((note_end(note), 0, track_name, note))
    events.sort(key=lambda event: (event[0], event[1], event[2]))

    active: dict[str, dict[str, Any]] = {}
    snapshots: list[tuple[float, dict[str, dict[str, Any]]]] = []
    index = 0
    while index < len(events):
        time = events[index][0]
        while index < len(events) and events[index][0] == time:
            _, kind, track_name, note = events[index]
            if kind == 0:
                active.pop(track_name, None)
            else:
                active[track_name] = note
            index += 1
        if active:
            snapshots.append((time, dict(active)))
    return snapshots


def assess_pad_writing(
    tracks: dict[str, list[dict[str, Any]]],
    coverage: dict[str, Any],
) -> tuple[dict[str, Any], list[str], list[str], dict[str, Any]]:
    deductions = 0
    warnings: list[str] = []
    suggestions: list[str] = []
    issue_categories = 0
    metrics: dict[str, Any] = {}
    gate_failures: list[dict[str, Any]] = []

    if not any(tracks[track_name] for track_name in PAD_TRACKS):
        metrics["statusReason"] = "No pad notes are available for writing-quality review."
        return (
            category_result(
                "padWriting", 0, floor=0, applicable=False, status="notEvaluable"
            ),
            warnings,
            suggestions,
            metrics,
        )

    if not bool(coverage.get("evidenceSufficient", True)):
        metrics["statusReason"] = (
            "Pad presence evidence is insufficient for a separate writing-quality score."
        )
        return (
            category_result(
                "padWriting", 0, floor=0, applicable=False, status="notEvaluable"
            ),
            warnings,
            suggestions,
            metrics,
        )

    repeated_tracks: list[str] = []
    for track_name in PAD_TRACKS:
        midis = [int(note["midi"]) for note in tracks[track_name]]
        if len(midis) >= 4 and len(set(midis)) / len(midis) <= 0.25:
            repeated_tracks.append(track_name)
    metrics["repeatedTracks"] = repeated_tracks
    if repeated_tracks:
        warnings.append(f"Pad voices have prolonged pitch repetition: {', '.join(repeated_tracks)}.")
        suggestions.append("Give repeated pad voices more common-tone releases or stepwise voice leading.")
        deductions += min(8, len(repeated_tracks) * 2)
        issue_categories += 1

    range_issues: list[str] = []
    range_issue_count = 0
    range_issue_rates: dict[str, float] = {}
    for track_name, (low, high) in PAD_PREFERRED_RANGES.items():
        outside = sum(
            1
            for note in tracks[track_name]
            if not low <= int(note["midi"]) <= high
        )
        if outside:
            range_issues.append(f"{track_name}={outside}")
            range_issue_count += outside
            range_issue_rates[track_name] = rounded_metric(
                outside / max(1, len(tracks[track_name]))
            )
    metrics["rangeIssueRates"] = range_issue_rates
    if range_issues:
        warnings.append(f"Pad voices leave their preferred four-part registers: {', '.join(range_issues)}.")
        suggestions.append("Revoice pads into distinct soprano, alto, tenor, and bass-like registers.")
        total_pad_notes = sum(len(tracks[track_name]) for track_name in PAD_TRACKS)
        deductions += min(8, math.ceil(range_issue_count / max(total_pad_notes, 1) * 8))
        issue_categories += 1

    pad_large_leaps: list[str] = []
    pad_large_leap_count = 0
    pad_transition_count = 0
    pad_large_leaps_by_track: dict[str, int] = {}
    for track_name in PAD_TRACKS:
        notes = sorted(tracks[track_name], key=lambda note: float(note["time"]))
        pad_transition_count += max(0, len(notes) - 1)
        large_leaps = sum(
            1
            for index in range(1, len(notes))
            if not is_phrase_boundary(notes[index - 1], notes[index], [])
            and abs(int(notes[index]["midi"]) - int(notes[index - 1]["midi"])) > 7
        )
        if large_leaps:
            pad_large_leaps.append(f"{track_name}={large_leaps}")
            pad_large_leap_count += large_leaps
            pad_large_leaps_by_track[track_name] = large_leaps
    metrics["largeLeapsByTrack"] = pad_large_leaps_by_track
    metrics["largeLeapRate"] = rounded_metric(
        pad_large_leap_count / max(pad_transition_count, 1)
    )
    if pad_large_leaps:
        warnings.append(
            f"Pad voices have large non-boundary leaps: {', '.join(pad_large_leaps)}."
        )
        suggestions.append("Use common tones and stepwise motion to smooth pad voice leading.")
        deductions += min(
            8,
            math.ceil(pad_large_leap_count / max(pad_transition_count, 1) * 8),
        )
        issue_categories += 1

    active_beats = float(coverage["activeBeats"])
    incomplete_rate = float(coverage["fewerThanThreeBeats"]) / max(active_beats, 1e-9)
    crossing_rate = float(coverage["crossingBeats"]) / max(active_beats, 1e-9)
    wide_spacing_rate = float(coverage["wideSpacingBeats"]) / max(active_beats, 1e-9)
    metrics.update(
        {
            "incompleteActiveRate": rounded_metric(incomplete_rate),
            "crossingActiveRate": rounded_metric(crossing_rate),
            "wideSpacingActiveRate": rounded_metric(wide_spacing_rate),
        }
    )
    if incomplete_rate > 0.4:
        warnings.append(
            f"Pad texture uses fewer than three active voices for {incomplete_rate:.0%} of active pad time."
        )
        suggestions.append("Use fuller three- or four-part pad voicings when the pad texture is active.")
        deductions += min(8, math.ceil(incomplete_rate * 8))
        issue_categories += 1
    if crossing_rate > 0:
        warnings.append(
            f"Pad voice crossing appears for {float(coverage['crossingBeats']):g} beats."
        )
        suggestions.append("Keep the pad voice order stable unless a crossing is musically intentional.")
        deductions += min(10, max(1, math.ceil(crossing_rate * 10)))
        issue_categories += 1
    if wide_spacing_rate > 0:
        warnings.append(
            "Pad adjacent-voice spacing is unusually wide for "
            f"{float(coverage['wideSpacingBeats']):g} beats."
        )
        suggestions.append("Keep adjacent upper pad voices within an octave and control tenor-bass gaps.")
        deductions += min(8, max(1, math.ceil(wide_spacing_rate * 8)))
        issue_categories += 1

    snapshots = pad_snapshots(tracks)
    if snapshots:
        rigid_transitions = 0
        comparable_transitions = 0
        for (_, previous), (_, current) in zip(snapshots, snapshots[1:]):
            if set(previous) != set(PAD_TRACKS) or set(current) != set(PAD_TRACKS):
                continue
            deltas = [
                int(current[track_name]["midi"]) - int(previous[track_name]["midi"])
                for track_name in PAD_TRACKS
            ]
            if not any(deltas):
                continue
            comparable_transitions += 1
            nonzero = [delta for delta in deltas if delta]
            same_direction = all(delta > 0 for delta in nonzero) or all(
                delta < 0 for delta in nonzero
            )
            identical_motion = len(set(deltas)) == 1
            if identical_motion or (same_direction and len(nonzero) == len(PAD_TRACKS)):
                rigid_transitions += 1
        rigid_rate = rigid_transitions / max(1, comparable_transitions)
        metrics["comparableTransitions"] = comparable_transitions
        metrics["rigidTransitionRate"] = rounded_metric(rigid_rate)
        if comparable_transitions >= 2 and rigid_rate > 0.5:
            warnings.append(
                f"Pad voices move as rigid parallel blocks on {rigid_rate:.0%} of comparable transitions."
            )
            suggestions.append("Add common tones, contrary motion, and independent inner-voice movement.")
            deductions += min(10, math.ceil(rigid_rate * 10))
            issue_categories += 1

    evaluated_responses = int(coverage.get("evaluatedChordTransitions", 0))
    response_rate = float(coverage.get("harmonicChangeResponseRate", 0.0))
    unchanged_run = int(coverage.get("longestUnchangedVoicingRun", 0))
    metrics.update(
        {
            "evaluatedChordTransitions": evaluated_responses,
            "harmonicChangeResponseRate": rounded_metric(response_rate),
            "longestUnchangedVoicingRun": unchanged_run,
        }
    )
    response_score_cap: int | None = None
    if evaluated_responses >= 3 and response_rate < 0.25 - 1e-9:
        gate_failures.append(
            {
                "code": "PAD_HARMONIC_RESPONSE_FAILED",
                "track": "VocalPad",
                "observed": rounded_metric(response_rate),
                "required": 0.25,
                "suggestion": "Revoice at chord boundaries while preserving selected common tones.",
            }
        )
        warnings.append(
            f"Pad responds to only {response_rate:.0%} of {evaluated_responses} evaluated chord changes; "
            "required minimum is 25%."
        )
        suggestions.append("Revoice at chord boundaries while preserving selected common tones.")
        response_score_cap = int(CATEGORY_SPECS["padWriting"]["floor"]) - 1
    elif evaluated_responses >= 3 and (response_rate < 0.50 - 1e-9 or unchanged_run >= 3):
        warnings.append(
            f"Pad chord-change response is {response_rate:.0%}; longest unchanged voicing run is "
            f"{unchanged_run} transitions."
        )
        suggestions.append("Vary at least one inner voice across longer harmonic progressions.")
        response_score_cap = 18

    dense_bars = [
        int(bar) for bar, count in coverage["notesByBar"].items() if int(count) > 24
    ]
    metrics["denseBars"] = sorted(dense_bars)
    metrics["denseBarRate"] = rounded_metric(
        len(dense_bars) / max(len(coverage["notesByBar"]), 1)
    )
    if dense_bars:
        warnings.append(f"Pad note density is unusually high in bars {sorted(dense_bars)[:8]}.")
        suggestions.append("Reduce pad movement or let some voices sustain through dense bars.")
        deductions += min(
            8,
            math.ceil(len(dense_bars) / max(len(coverage["notesByBar"]), 1) * 8),
        )
        issue_categories += 1

    if issue_categories >= 3:
        warnings.append("Multiple pad-writing risks reduce the overall arrangement balance.")
        suggestions.append("Review the pad texture as a complete four-part arrangement, not as isolated tracks.")
        deductions += 4

    max_score = int(CATEGORY_SPECS["padWriting"]["maxScore"])
    metrics["deductions"] = min(max_score, deductions)
    metrics["gateFailures"] = gate_failures
    score = max_score - min(max_score, deductions)
    if response_score_cap is not None:
        score = min(score, response_score_cap)
    return (
        category_result("padWriting", score),
        warnings,
        suggestions,
        metrics,
    )


def quality_axis_result(
    name: str,
    score: int,
    *,
    applicable: bool = True,
    status: str = "scored",
) -> dict[str, Any]:
    floor = int(QUALITY_AXIS_SPECS[name]["floor"])
    resolved_score = max(0, min(100, int(score)))
    return {
        "score": resolved_score,
        "maxScore": 100,
        "floor": floor,
        "applicable": applicable,
        "status": status,
        "passedFloor": not applicable or resolved_score >= floor,
    }


def score_quality_axes(
    categories: dict[str, dict[str, Any]],
) -> tuple[int, int, list[str], dict[str, dict[str, Any]], list[str]]:
    """Keep harmonic correctness and musical structure non-compensating."""
    harmonic = categories["harmonicFit"]
    harmonic_applicable = bool(harmonic["applicable"])
    harmonic_score = round(
        100 * int(harmonic["score"]) / max(1, int(harmonic["maxScore"]))
    )
    harmonic_axis = quality_axis_result(
        "harmonicCorrectness",
        harmonic_score,
        applicable=harmonic_applicable,
        status="scored" if harmonic_applicable else "notEvaluable",
    )

    musical_categories = [
        category
        for name, category in categories.items()
        if name != "harmonicFit" and bool(category["applicable"])
    ]
    musical_earned = sum(int(category["score"]) for category in musical_categories)
    musical_available = sum(
        int(category["maxScore"]) for category in musical_categories
    )
    musical_applicable = musical_available > 0
    musical_score = round(100 * musical_earned / max(1, musical_available))
    if any(not bool(category["passedFloor"]) for category in musical_categories):
        musical_score = min(
            musical_score,
            int(QUALITY_AXIS_SPECS["musicalStructure"]["floor"]) - 1,
        )
    musical_axis = quality_axis_result(
        "musicalStructure",
        musical_score,
        applicable=musical_applicable,
        status="scored" if musical_applicable else "notEvaluable",
    )
    axes = {
        "harmonicCorrectness": harmonic_axis,
        "musicalStructure": musical_axis,
    }
    applicable_axis_scores = [
        int(axis["score"]) for axis in axes.values() if bool(axis["applicable"])
    ]
    raw_score = min(applicable_axis_scores, default=0)
    category_floor_failures = [
        name
        for name, category in categories.items()
        if bool(category["applicable"]) and not bool(category["passedFloor"])
    ]
    axis_floor_failures = [
        name
        for name, axis in axes.items()
        if bool(axis["applicable"]) and not bool(axis["passedFloor"])
    ]
    final_score = (
        min(raw_score, DELIVERY_FLOOR_CAP)
        if category_floor_failures or axis_floor_failures
        else raw_score
    )
    return (
        final_score,
        raw_score,
        category_floor_failures,
        axes,
        axis_floor_failures,
    )


def score_arrangement(
    bv_path: Path,
    payload: dict[str, Any],
    source: dict[str, Any],
    pad_policy: str = "sectional",
) -> dict[str, Any]:
    warnings: list[str] = []
    suggestions: list[str] = []
    header = payload["header"]
    tracks = payload["tracks"]
    melody = source.get("melody") if isinstance(source.get("melody"), list) else []
    control = control_spans(source)
    segments = build_time_signature_segments(header)
    events = chord_events(bv_path.parent / "note.txt", header)
    harmony_lead = [
        lead_note
        for source_note, lead_note in zip(melody, tracks["LeadDouble"])
        if section_at(control, float(source_note["time"])) != "no_bv"
    ]
    harmony_shape, harmony_warnings, harmony_suggestions, harmony_metrics = (
        analyze_harmony_shape(
            tracks, harmony_lead, control, segments, events
        )
    )
    warnings.extend(harmony_warnings)
    suggestions.extend(harmony_suggestions)

    structure_penalty = 0.0
    structure_metrics: dict[str, Any] = {
        "wideSpacingCounts": {},
        "wideSpacingRates": {},
        "outsidePreferredRates": {},
    }
    if melody:
        harmony_melody = harmony_lead
        for track_name in HIGH_TRACKS:
            wide = sum(
                1
                for melody_note, harmony_note in zip(harmony_melody, tracks[track_name])
                if int(harmony_note["midi"]) - int(melody_note["midi"]) > 12
            )
            rate = wide / max(1, len(tracks[track_name]))
            structure_metrics["wideSpacingRates"][track_name] = rounded_metric(rate)
            if wide:
                structure_metrics["wideSpacingCounts"][track_name] = wide
                warnings.append(f"{track_name} sits more than an octave above melody on {wide} notes.")
                structure_penalty += rate * 2
        for track_name in LOW_TRACKS:
            wide = sum(
                1
                for melody_note, harmony_note in zip(harmony_melody, tracks[track_name])
                if int(melody_note["midi"]) - int(harmony_note["midi"]) > 12
            )
            rate = wide / max(1, len(tracks[track_name]))
            structure_metrics["wideSpacingRates"][track_name] = rounded_metric(rate)
            if wide:
                structure_metrics["wideSpacingCounts"][track_name] = wide
                warnings.append(f"{track_name} sits more than an octave below melody on {wide} notes.")
                structure_penalty += rate * 2

        for track_name in CONTOUR_TRACKS:
            outside_preferred = sum(
                1
                for melody_note, harmony_note in zip(harmony_melody, tracks[track_name])
                if not 2
                <= abs(int(harmony_note["midi"]) - int(melody_note["midi"]))
                <= 6
            )
            rate = outside_preferred / max(1, len(tracks[track_name]))
            structure_metrics["outsidePreferredRates"][track_name] = rounded_metric(rate)
            if rate > 0.25:
                warnings.append(
                    f"{track_name} falls outside the preferred 2-4 scale-degree spacing "
                    f"on {rate:.0%} of aligned notes."
                )
                suggestions.append(
                    f"Keep most {track_name} notes within roughly 2-6 semitones of the melody; "
                    "use fifth-degree spacing only occasionally."
                )
                structure_penalty += (rate - 0.25) / 0.75 * 2

    harmonic_fit, fit_warnings, fit_suggestions, harmonic_fit_metrics = (
        analyze_harmonic_fit(tracks, events, float(header["duration"]))
    )
    warnings.extend(fit_warnings)
    suggestions.extend(fit_suggestions)
    coverage = pad_timeline_metrics(
        tracks,
        float(header["duration"]),
        control,
        melody,
        events,
        segments,
    )
    pad_presence, presence_warnings, presence_suggestions, presence_metrics = (
        score_pad_presence(coverage, pad_policy, bool(control))
    )
    writing_coverage = {**coverage, **presence_metrics}
    pad_writing, pad_warnings, pad_suggestions, pad_writing_metrics = assess_pad_writing(
        tracks, writing_coverage
    )
    warnings.extend(presence_warnings)
    warnings.extend(pad_warnings)
    suggestions.extend(presence_suggestions)
    suggestions.extend(pad_suggestions)
    development_penalty, development_warnings, development_suggestions, development_metrics = (
        analyze_section_development(coverage)
    )
    structure_penalty += development_penalty
    structure_metrics["sectionDevelopment"] = development_metrics
    warnings.extend(development_warnings)
    suggestions.extend(development_suggestions)

    structure_max = int(CATEGORY_SPECS["structureAndPlayability"]["maxScore"])
    applied_structure_deductions = min(
        structure_max, math.ceil(structure_penalty)
    )
    categories = {
        "harmonyShape": harmony_shape,
        "padPresence": pad_presence,
        "padWriting": pad_writing,
        "harmonicFit": harmonic_fit,
        "structureAndPlayability": category_result(
            "structureAndPlayability",
            structure_max - applied_structure_deductions,
        ),
    }
    (
        score,
        raw_score,
        floor_failures,
        quality_axes,
        quality_axis_failures,
    ) = score_quality_axes(categories)
    musical_gate_failures = [
        {
            **failure,
            "category": category,
        }
        for category, category_metrics in (
            ("harmonyShape", harmony_metrics),
            ("padPresence", presence_metrics),
            ("padWriting", pad_writing_metrics),
            ("harmonicFit", harmonic_fit_metrics),
        )
        for failure in category_metrics.get("gateFailures", [])
    ]

    if not warnings:
        suggestions.append("No soft music issues detected by the current heuristic checks.")

    structure_metrics["normalizedPenalty"] = rounded_metric(structure_penalty)
    structure_metrics["deductions"] = applied_structure_deductions
    return {
        "score": score,
        "rawScoreBeforeFloorCap": raw_score,
        "floorFailures": floor_failures,
        "qualityAxes": quality_axes,
        "qualityAxisFailures": quality_axis_failures,
        "musicalGateFailures": musical_gate_failures,
        "categoryScores": categories,
        "metrics": {
            "harmonyShape": harmony_metrics,
            "padPresence": {**coverage, **presence_metrics},
            "padWriting": pad_writing_metrics,
            "harmonicFit": harmonic_fit_metrics,
            "structureAndPlayability": structure_metrics,
        },
        "warnings": warnings,
        "suggestions": suggestions,
    }


def delivery_blockers(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Return ordered, actionable reasons that prevent this report from passing."""
    blockers: list[dict[str, Any]] = []
    report_suggestions = report.get("suggestions", [])
    fallback_suggestion = (
        str(report_suggestions[0])
        if isinstance(report_suggestions, list) and report_suggestions
        else "Fix this issue and rerun validation."
    )
    hard_errors = report.get("hardErrors", [])
    if isinstance(hard_errors, list):
        hard_suggestion = (
            fallback_suggestion
            if len(hard_errors) == 1
            else "Fix each hard validation issue, then rerun validation before musical revisions."
        )
        for error in hard_errors:
            blockers.append(
                {
                    "kind": "hardError",
                    "code": "HARD_VALIDATION_FAILED",
                    "message": str(error),
                    "suggestion": hard_suggestion,
                }
            )

    gate_failures = report.get("musicalGateFailures", [])
    if isinstance(gate_failures, list):
        for failure in gate_failures:
            if not isinstance(failure, dict):
                continue
            blockers.append({"kind": "musicalGate", **failure})

    category_scores = report.get("categoryScores", {})
    if not isinstance(category_scores, dict):
        category_scores = {}
    floor_failures = report.get("floorFailures", [])
    if isinstance(floor_failures, list):
        for category_value in floor_failures:
            category = str(category_value)
            result = category_scores.get(category, {})
            if not isinstance(result, dict):
                result = {}
            blockers.append(
                {
                    "kind": "categoryFloor",
                    "code": "CATEGORY_FLOOR_FAILED",
                    "category": category,
                    "observed": result.get("score"),
                    "required": result.get("floor"),
                    "suggestion": CATEGORY_REVISION_GUIDANCE.get(
                        category, "Revise this category and rerun validation."
                    ),
                }
            )

    quality_axes = report.get("qualityAxes", {})
    if not isinstance(quality_axes, dict):
        quality_axes = {}
    quality_axis_failures = report.get("qualityAxisFailures", [])
    if isinstance(quality_axis_failures, list):
        for axis_value in quality_axis_failures:
            axis = str(axis_value)
            result = quality_axes.get(axis, {})
            if not isinstance(result, dict):
                result = {}
            blockers.append(
                {
                    "kind": "qualityAxisFloor",
                    "code": "QUALITY_AXIS_FLOOR_FAILED",
                    "axis": axis,
                    "observed": result.get("score"),
                    "required": result.get("floor"),
                    "suggestion": QUALITY_AXIS_REVISION_GUIDANCE.get(
                        axis, "Revise this quality axis and rerun validation."
                    ),
                }
            )

    if bool(report.get("policyOverridden")):
        blockers.append(
            {
                "kind": "policyOverride",
                "code": "DIAGNOSTIC_POLICY_OVERRIDE",
                "observed": report.get("padPolicy"),
                "required": "bv_plan.json padPolicy",
                "suggestion": (
                    "Update bv_plan.json and rerender if the override is the intended policy."
                ),
            }
        )

    if report.get("scoreVersion") != "bv-diagnostics-v2" and bool(report.get("deliveryEligible")) and not bool(
        report.get("scoreThresholdMet")
    ):
        blockers.append(
            {
                "kind": "scoreThreshold",
                "code": "SCORE_THRESHOLD_NOT_MET",
                "observed": report.get("score"),
                "required": report.get("minScore"),
                "suggestion": (
                    "Review the lowest-scoring applicable category; revise only when musically justified, "
                    "otherwise confirm that the requested threshold reflects delivery intent."
                ),
            }
        )
    return blockers


def finalize_report(report: dict[str, Any]) -> dict[str, Any]:
    finalized = {
        "passed": False, "deliveryEligible": False, "scoreThresholdMet": False,
        "score": 0, "hardErrors": [], "warnings": [], "suggestions": [],
        **report,
    }
    if finalized.get("scoreVersion") == "bv-diagnostics-v2":
        finalized.update(score=None, minScore=None, scoreThresholdMet=None)
    blockers = delivery_blockers(finalized)
    finalized["deliveryBlockers"] = blockers
    finalized["workflowStatus"] = (
        "blocked"
        if blockers
        else "reviewRecommended"
        if finalized.get("warnings")
        else "ready"
    )
    return finalized


def diagnose_music(payload: dict[str, Any], chord_spans: list[tuple], control: list[dict]) -> dict:
    tracks = {name: sorted(notes, key=lambda n: float(n["time"]))
              for name, notes in payload["tracks"].items()}
    duration = float(payload["header"]["duration"])
    lead_by_time = {float(n["time"]): n for n in tracks["LeadDouble"]}
    evidence = {}
    events = defaultdict(list)
    events[0.0]
    events[duration]
    for name, notes in tracks.items():
        if name == "LeadDouble":
            continue
        intervals, outside, jumps, conflicts = [], [], [], []
        adjacent = repeated = 0
        chord_beats = non_chord_beats = 0.0
        span_index = 0
        previous = None
        for note in notes:
            start, end = float(note["time"]), float(note["time"]) + float(note["duration"])
            pitch = int(note["midi"])
            events[start].append((name, pitch, 1))
            events[end].append((name, pitch, -1))
            if intervals and start <= intervals[-1][1] + 1e-9:
                intervals[-1][1] = max(end, intervals[-1][1])
            else:
                intervals.append([start, end])
            if name in MELODY_ALIGNED_TRACKS:
                delta = pitch - int(lead_by_time[start]["midi"])
                if abs(delta) > 12:
                    outside.append({"time": start, "duration": end-start, "semitones": delta})
            # A rest breaks the line: do not count transitions across silence.
            if previous and abs(start - float(previous["time"]) - float(previous["duration"])) < 1e-9:
                adjacent += 1
                delta = pitch - int(previous["midi"])
                repeated += delta == 0
                if abs(delta) > 7:
                    jumps.append({"fromTime": previous["time"], "time": start, "semitones": delta})
            previous = note
            while span_index < len(chord_spans) and chord_spans[span_index][1] <= start:
                span_index += 1
            index = span_index
            while index < len(chord_spans) and chord_spans[index][0] < end:
                left, right, pcs, chord = chord_spans[index]
                a, b = max(start, left), min(end, right)
                if b > a and pcs is not None:
                    chord_beats += b-a
                    if pitch % 12 not in pcs:
                        non_chord_beats += b-a
                        conflicts.append({"time": a, "end": b, "midi": pitch, "chord": chord})
                index += 1
        evidence[name] = {
            "noteCount": len(notes), "activitySpans": intervals,
            "activeBeats": sum(b-a for a, b in intervals),
            "adjacentTransitionCount": adjacent,
            "repeatedPitchRate": repeated / adjacent if adjacent else None,
            "contourEvidence": "available" if adjacent >= 4 else "insufficient",
            "evidenceNote": "Contour evidence is available after four adjacent transitions.",
            "outsideOctave": outside,
            "outsideOctaveRate": len(outside)/len(notes) if notes and name in MELODY_ALIGNED_TRACKS else None,
            "largeLeaps": jumps, "chordEvaluatedBeats": chord_beats,
            "nonChordBeats": non_chord_beats,
            "nonChordRate": non_chord_beats/chord_beats if chord_beats else None,
            "nonChordSpans": conflicts,
        }
    active = {}
    timeline = []
    times = sorted(events)
    for i, time in enumerate(times[:-1]):
        # End events before onsets at the same boundary.
        for name, pitch, change in sorted(events[time], key=lambda e: e[2]):
            if change < 0:
                active.pop(name, None)
            else:
                active[name] = pitch
        end = times[i+1]
        names = sorted(active)
        if timeline and timeline[-1]["tracks"] == names:
            timeline[-1]["end"] = end
        else:
            timeline.append({"time": time, "end": end, "tracks": names})
    pairs = []
    for first, second in combinations(MELODY_ALIGNED_TRACKS, 2):
        a, b = tracks[first], tracks[second]
        i = j = 0
        overlap = duplicate = 0.0
        spans = []
        while i < len(a) and j < len(b):
            left = max(float(a[i]["time"]), float(b[j]["time"]))
            ae = float(a[i]["time"]) + float(a[i]["duration"])
            be = float(b[j]["time"]) + float(b[j]["duration"])
            right = min(ae, be)
            if right > left:
                overlap += right-left
                if a[i]["midi"] == b[j]["midi"]:
                    duplicate += right-left
                    spans.append({"time": left, "end": right})
            if ae <= be:
                i += 1
            else:
                j += 1
        pairs.append({"tracks": [first, second], "overlapBeats": overlap,
                      "unisonBeats": duplicate, "unisonRate": duplicate/overlap if overlap else None,
                      "unisonSpans": spans})
    sections = []
    for section in control:
        if "time" not in section or "duration" not in section:
            continue
        start = float(section["time"])
        end = start + float(section["duration"])
        sections.append({"section": section.get("section"), "time": start, "end": end,
                         "activeBeatsByTrack": {
                             name: sum(max(0, min(end,b)-max(start,a)) for a,b in info["activitySpans"])
                             for name,info in evidence.items()}})
    return {"tracks": evidence, "voiceCombinations": timeline, "pairs": pairs, "sections": sections,
            "interpretation": "Review non-chord tones, repetition, leaps and sparse coverage in musical context, using the reported sample sizes."}


def validate(
    path: Path,
    min_score: int = 80,
    pad_policy: str | None = None,
    *,
    allow_policy_override: bool = False,
    evaluation_version: str = "v1",
) -> tuple[dict[str, Any], int]:
    common = {
        "scoreVersion": "bv-diagnostics-v2" if evaluation_version == "v2" else SCORE_VERSION,
        "minScore": min_score,
        "padPolicy": pad_policy or "sectional",
        "requestedPadPolicy": pad_policy,
        "policySource": "cli" if pad_policy is not None else "default",
        "policyOverridden": False,
        "planDetected": False,
        "planFingerprintVerified": None,
        "rawMatchesPlan": None,
        "categoryScores": {}, "qualityAxes": {}, "qualityAxisFailures": [],
        "metrics": {}, "floorFailures": [], "musicalGateFailures": [],
    }
    if evaluation_version == "v2":
        common["evaluationMode"] = "hard-validation-and-review"
    if evaluation_version not in ("v1", "v2"):
        return finalize_report({
            **common, "hardErrors": ["evaluation_version must be v1 or v2."],
            "suggestions": ["Use --evaluation-version v1 or v2."],
        }), 1
    if isinstance(min_score, bool) or not isinstance(min_score, int) or not 0 <= min_score <= 100:
        return finalize_report({
            **common, "hardErrors": ["min_score must be an integer between 0 and 100."],
            "suggestions": ["Use a valid minimum score and rerun validation."],
        }), 1
    if pad_policy is not None and pad_policy not in PAD_POLICIES:
        return finalize_report({
            **common, "hardErrors": [f"pad_policy must be one of: {', '.join(PAD_POLICIES)}."],
            "suggestions": ["Use a valid pad policy and rerun validation."],
        }), 1
    if not isinstance(allow_policy_override, bool) or (allow_policy_override and pad_policy is None):
        error = ("allow_policy_override must be a boolean." if not isinstance(allow_policy_override, bool)
                 else "allow_policy_override requires an explicit pad_policy.")
        return finalize_report({
            **common, "hardErrors": [error],
            "suggestions": ["Specify --pad-policy together with the override flag."],
        }), 1
    if evaluation_version == "v2" and (pad_policy is not None or allow_policy_override):
        return finalize_report({
            **common, "hardErrors": ["v2 uses free density; legacy pad policy overrides are not supported."],
            "suggestions": ["Run v2 diagnostics without Pad policy overrides."],
        }), 1

    bv_path = resolve_bv_path(path)
    plan_path = bv_path.parent / "bv_plan.json"
    try:
        common["planDetected"] = plan_path.exists()
        if common["planDetected"]:
            common["planFingerprintVerified"] = False
        source = bv_raw_to_midi.load_source_json(bv_path.parent, required=True)
        if not isinstance(source.get("melody"), list):
            raise ValueError("Source JSON must contain a melody list.")
    except Exception as exc:
        return finalize_report({
            **common, "hardErrors": [f"Unable to read source melody/canonical JSON: {exc}"],
            "suggestions": ["Fix source JSON files before revising musical details."],
        }), 1

    plan = None
    expected_payload = None
    if common["planDetected"]:
        try:
            _, plan, expected_payload = bv_raw_to_midi.prepare_plan_render(plan_path, source=source)
        except Exception as exc:
            return finalize_report({
                **common, "hardErrors": [f"Unable to verify sibling bv_plan.json: {exc}"],
                "suggestions": ["Fix the plan or its source fingerprint, then rerender bv_raw.json."],
            }), 1
        common.update(planFingerprintVerified=True, padPolicy=plan["padPolicy"], policySource="plan")

    sparse = plan is not None and plan["schemaVersion"] == "bv-plan-v2"
    payload, hard_errors = hard_validate(bv_path, source, sparse=sparse)
    if payload is None:
        return finalize_report({
            **common, "hardErrors": hard_errors,
            "suggestions": ["Fix hard validation errors before revising musical details."],
        }), 1
    if plan is not None:
        common["rawMatchesPlan"] = payload == expected_payload
        if not common["rawMatchesPlan"]:
            return finalize_report({
                **common, "hardErrors": [*hard_errors, "bv_raw.json does not match the current bv_plan.json expansion."],
                "suggestions": ["Rerender bv_raw.json from the current plan before validation."],
            }), 1
    if sparse and evaluation_version == "v1":
        return finalize_report({
            **common, "hardErrors": [*hard_errors, "bv-plan-v2 requires --evaluation-version v2."],
            "suggestions": ["Use v2 diagnostics for this sparse plan."],
        }), 1
    if evaluation_version == "v1" and plan is not None and pad_policy is not None and pad_policy != plan["padPolicy"]:
        if not allow_policy_override:
            return finalize_report({
                **common, "hardErrors": [f"Requested pad_policy '{pad_policy}' conflicts with bv_plan.json padPolicy '{plan['padPolicy']}'."],
                "suggestions": ["Use the plan policy, update and rerender the plan, or use the explicit diagnostic override."],
            }), 1
        common.update(padPolicy=pad_policy, policySource="cliOverride", policyOverridden=True)

    if evaluation_version == "v2":
        try:
            events = chord_events(bv_path.parent / "note.txt", payload["header"])
            spans = chord_spans(events, float(payload["header"]["duration"]))
            metrics = diagnose_music(payload, spans, source.get("control", []))
        except Exception as exc:
            hard_errors.append(f"Unable to produce v2 diagnostics: {exc}")
            metrics = {}
        passed = not hard_errors
        return finalize_report({
            **common, "passed": passed, "hardValidationPassed": passed, "deliveryEligible": passed,
            "hardErrors": hard_errors, "metrics": metrics,
            "warnings": ["Independent symbolic music review is pending."],
            "suggestions": ["Review source and arrangement independently before consulting diagnostics; see skill/background-vocals/references/music-review.md."],
            "musicReviewStatus": "pending", "musicReviewBlocking": False,
        }), 0 if passed else 1

    try:
        scoring = score_arrangement(bv_path, payload, source, common["padPolicy"])
    except Exception as exc:
        return finalize_report({
            **common, "hardErrors": [*hard_errors, f"Unable to score arrangement: {exc}"],
            "suggestions": ["Fix sidecar files used by soft scoring, then rerun validation."],
        }), 1
    delivery_eligible = not (
        hard_errors or scoring["musicalGateFailures"] or scoring["floorFailures"]
        or scoring["qualityAxisFailures"] or common["policyOverridden"]
    )
    score_threshold_met = scoring["score"] >= min_score
    passed = delivery_eligible and score_threshold_met
    if common["policyOverridden"]:
        scoring["warnings"].append("Pad policy was overridden for diagnostic scoring; this report is not a delivery pass.")
        scoring["suggestions"].append("Update bv_plan.json and rerender if the override reflects the intended delivery policy.")
    return finalize_report({
        **common, "passed": passed, "deliveryEligible": delivery_eligible,
        "scoreThresholdMet": score_threshold_met, "score": scoring["score"],
        "hardErrors": hard_errors, "categoryScores": scoring["categoryScores"],
        "qualityAxes": scoring["qualityAxes"], "qualityAxisFailures": scoring["qualityAxisFailures"],
        "metrics": {**scoring["metrics"], "scoring": {"rawScoreBeforeFloorCap": scoring["rawScoreBeforeFloorCap"]}},
        "floorFailures": scoring["floorFailures"], "musicalGateFailures": scoring["musicalGateFailures"],
        "warnings": scoring["warnings"], "suggestions": scoring["suggestions"],
    }), 0 if passed else 1


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate and score bv_raw.json.")
    parser.add_argument("path", help="Path to output/<work_name>/ or bv_raw.json.")
    parser.add_argument("--evaluation-version", choices=("v1", "v2"), default="v1",
                        help="Evaluation version: v1 scoring (default), or v2 music diagnostics.")
    parser.add_argument("--min-score", type=int, default=80, help="Legacy/v1 minimum passing score; v2 has no numerical quality gate.")
    parser.add_argument(
        "--pad-policy",
        choices=PAD_POLICIES,
        help=(
            "Pad presence policy. Defaults to bv_plan.json when present, otherwise sectional."
        ),
    )
    parser.add_argument(
        "--allow-policy-override",
        action="store_true",
        help=(
            "Allow diagnostic scoring with a policy that conflicts with bv_plan.json; "
            "the report cannot pass delivery."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    report, exit_code = validate(
        Path(args.path).expanduser(),
        args.min_score,
        args.pad_policy,
        allow_policy_override=args.allow_policy_override,
        evaluation_version=args.evaluation_version,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
