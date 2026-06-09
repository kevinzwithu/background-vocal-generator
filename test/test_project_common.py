from __future__ import annotations

import unittest
from dataclasses import dataclass

from script import project_common


@dataclass(frozen=True)
class Segment:
    start: float
    bar_length: float


class ProjectCommonTest(unittest.TestCase):
    def test_time_signature_changes_share_bar_and_grid_calculations(self) -> None:
        segments = project_common.build_time_signature_segments([
            {"time": 0, "numerator": 3, "denominator": 4},
            {"time": 6, "numerator": 6, "denominator": 8},
        ])
        self.assertEqual([segment.start_bar for segment in segments], [1, 3])
        self.assertEqual(project_common.bar_and_beat(segments, 6), (3, 1))
        self.assertEqual(project_common.bar_and_beat(segments, 10.5), (4, 2.5))
        self.assertEqual(segments[1].grid_size, 3 / 32)
        with self.assertRaisesRegex(ValueError, "previous bar boundary"):
            project_common.build_time_signature_segments([
                {"time": 0, "numerator": 4, "denominator": 4},
                {"time": 3, "numerator": 3, "denominator": 4},
            ])

    def test_validates_tolerance_boundaries(self) -> None:
        for value in (0, 0.01, project_common.MAX_TOLERANCE):
            with self.subTest(value=value):
                self.assertTrue(project_common.is_valid_tolerance(value))

        for value in (
            True,
            "0.01",
            -1,
            float("nan"),
            float("inf"),
            project_common.MAX_TOLERANCE + 0.001,
        ):
            with self.subTest(value=value):
                self.assertFalse(project_common.is_valid_tolerance(value))

    def test_counts_bars_across_time_signature_segments(self) -> None:
        segments = [Segment(0, 3), Segment(6, 4)]

        self.assertEqual(project_common.count_bars(14, segments), 4)
        self.assertEqual(project_common.count_bars(12, segments), 4)
        self.assertEqual(project_common.count_bars(6.1, segments), 3)

    def test_converts_tempo_map_to_seconds(self) -> None:
        tempo_map = [{"time": 0, "bpm": 120}, {"time": 4, "bpm": 60}]

        self.assertEqual(project_common.duration_seconds(8, tempo_map), 6)
        self.assertEqual(project_common.duration_seconds(2, tempo_map), 1)
        self.assertEqual(
            project_common.piece_metrics(8, [Segment(0, 4)], tempo_map),
            (2, 6),
        )

    def test_segment_lookup_is_logarithmic(self) -> None:
        class CountingSegments(list[Segment]):
            def __init__(self, values: list[Segment]) -> None:
                super().__init__(values)
                self.lookups = 0

            def __getitem__(self, index: int) -> Segment:
                self.lookups += 1
                return super().__getitem__(index)

        segments = CountingSegments([Segment(float(index), 1) for index in range(4096)])

        selected = project_common.segment_for_time(segments, 3000.5)

        self.assertEqual(selected.start, 3000)
        self.assertLessEqual(segments.lookups, 20)

    def test_snap_to_music_grid_accepts_straight_and_triplet_positions(self) -> None:
        bar_length = 4.0
        numerator = 4
        tolerance = 0.01

        for offset in (0.125, 1 / 3, 1 / 6, 2 + 1 / 3):
            with self.subTest(offset=offset):
                ok, nearest = project_common.snap_to_music_grid(
                    offset,
                    bar_length,
                    numerator,
                    tolerance,
                )
                self.assertTrue(ok)
                self.assertAlmostEqual(nearest, offset, places=6)

        ok, _ = project_common.snap_to_music_grid(0.2, bar_length, numerator, tolerance)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
