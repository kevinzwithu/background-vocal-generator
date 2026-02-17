"""Shared deterministic helpers for project music timelines and limits."""

from __future__ import annotations

import math
from typing import Any, Protocol, Sequence, TypeVar


MAX_BARS = 250
MAX_SECONDS = 10 * 60
MAX_TEMPO_EVENTS = 2_000
MAX_TIME_SIGNATURE_EVENTS = 1_000
MAX_TIME_SIGNATURE_BYTE = 0xFF
MAX_TOLERANCE = 0.05
EPSILON = 1e-9
STRAIGHT_GRID_DIVISIONS_PER_BAR = 32
GRID_LABEL = "1/32_bar"
TRIPLET_SUBDIVISIONS_PER_BEAT = 6


class TimelineSegment(Protocol):
    start: float
    bar_length: float


Segment = TypeVar("Segment", bound=TimelineSegment)


def straight_grid_size_beats(bar_length: float) -> float:
    return bar_length / STRAIGHT_GRID_DIVISIONS_PER_BAR


def triplet_grid_size_beats(bar_length: float, numerator: int) -> float:
    return bar_length / (numerator * TRIPLET_SUBDIVISIONS_PER_BEAT)


def snap_to_music_grid(
    offset: float,
    bar_length: float,
    numerator: int,
    tolerance: float,
) -> tuple[bool, float]:
    best_nearest = offset
    best_distance = float("inf")
    matched = False
    for grid_size in (
        straight_grid_size_beats(bar_length),
        triplet_grid_size_beats(bar_length, numerator),
    ):
        nearest_index = round(offset / grid_size)
        nearest = nearest_index * grid_size
        distance = abs(offset - nearest)
        if distance < best_distance:
            best_distance = distance
            best_nearest = nearest
        if distance <= tolerance:
            matched = True
    return matched, best_nearest


def normalize_number(value: float, digits: int = 6) -> int | float:
    if abs(value - round(value)) < EPSILON:
        return int(round(value))
    return round(value, digits)


def is_valid_tolerance(tolerance: object) -> bool:
    return (
        isinstance(tolerance, (int, float))
        and not isinstance(tolerance, bool)
        and math.isfinite(float(tolerance))
        and 0 <= tolerance <= MAX_TOLERANCE
    )


def segment_for_time(segments: Sequence[Segment], time: float) -> Segment:
    low = 0
    high = len(segments)
    target = time + EPSILON
    while low < high:
        middle = (low + high) // 2
        if segments[middle].start <= target:
            low = middle + 1
        else:
            high = middle
    return segments[max(0, low - 1)]


def count_bars(duration: float, segments: Sequence[TimelineSegment]) -> int:
    bar_count = 0
    for index, segment in enumerate(segments):
        next_start = segments[index + 1].start if index + 1 < len(segments) else duration
        segment_end = min(duration, next_start)
        elapsed = max(0.0, segment_end - segment.start)
        bar_count += math.ceil((elapsed - EPSILON) / segment.bar_length)
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
        seconds += (segment_end - start) * 60 / float(item["bpm"])
    return seconds


def piece_metrics(
    duration: float,
    segments: Sequence[TimelineSegment],
    tempo_map: list[dict[str, Any]],
) -> tuple[int, float]:
    return count_bars(duration, segments), duration_seconds(duration, tempo_map)
