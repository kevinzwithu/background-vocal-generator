from __future__ import annotations

import importlib.util
import io
import json
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "script"
SCRIPT_PATH = SCRIPT_DIR / "validate_bv_music.py"


def load_validate_bv_music_module() -> Any:
    if str(SCRIPT_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPT_DIR))
    spec = importlib.util.spec_from_file_location("validate_bv_music", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


validate_bv_music = load_validate_bv_music_module()
SPRINT2_FIXTURE = json.loads(
    (ROOT / "test" / "fixtures" / "sprint2_cases.json").read_text(encoding="utf-8")
)


def header() -> dict[str, Any]:
    return {
        "startTime": 0,
        "duration": 2,
        "timeUnit": "beats",
        "grid": "1/32_bar",
        "tempoMap": [{"time": 0, "bpm": 120}],
        "timeSignatureMap": [{"time": 0, "numerator": 4, "denominator": 4}],
    }


def variable_time_signature_header() -> dict[str, Any]:
    return {
        "startTime": 0,
        "duration": 14,
        "timeUnit": "beats",
        "grid": "1/32_bar",
        "tempoMap": [{"time": 0, "bpm": 120}],
        "timeSignatureMap": [
            {"time": 0, "numerator": 3, "denominator": 4},
            {"time": 6, "numerator": 4, "denominator": 4},
        ],
    }


def melody() -> list[dict[str, Any]]:
    return [
        {"name": "C4", "midi": 60, "time": 0, "duration": 1},
        {"name": "D4", "midi": 62, "time": 1, "duration": 1},
    ]


def tracks() -> dict[str, list[dict[str, Any]]]:
    return {
        "LeadDouble": melody(),
        "HHA": [
            {"name": "E4", "midi": 64, "time": 0, "duration": 1},
            {"name": "F4", "midi": 65, "time": 1, "duration": 1},
        ],
        "HHB": [
            {"name": "G4", "midi": 67, "time": 0, "duration": 1},
            {"name": "A4", "midi": 69, "time": 1, "duration": 1},
        ],
        "HHF": [
            {"name": "C5", "midi": 72, "time": 0, "duration": 1},
            {"name": "C5", "midi": 72, "time": 1, "duration": 1},
        ],
        "LHA": [
            {"name": "E3", "midi": 52, "time": 0, "duration": 1},
            {"name": "F3", "midi": 53, "time": 1, "duration": 1},
        ],
        "LHB": [
            {"name": "G3", "midi": 55, "time": 0, "duration": 1},
            {"name": "A3", "midi": 57, "time": 1, "duration": 1},
        ],
        "LHF": [
            {"name": "C3", "midi": 48, "time": 0, "duration": 1},
            {"name": "C3", "midi": 48, "time": 1, "duration": 1},
        ],
        "VocalPad1": [],
        "VocalPad2": [],
        "VocalPad3": [],
        "VocalPad4": [],
    }


def sectional_header() -> dict[str, Any]:
    return {
        **header(),
        "duration": 16,
    }


def sectional_melody() -> list[dict[str, Any]]:
    return [
        {"name": "C4", "midi": 60, "time": time, "duration": 1}
        for time in range(0, 16, 2)
    ]


def sectional_tracks(
    pad_blocks: list[tuple[float, float, tuple[int, int, int, int]]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    source = sectional_melody()

    def aligned(midi: int) -> list[dict[str, Any]]:
        return [
            {
                "name": validate_bv_music.midi_name(midi),
                "midi": midi,
                "time": note["time"],
                "duration": note["duration"],
            }
            for note in source
        ]

    result = {
        "LeadDouble": [dict(note) for note in source],
        "HHA": aligned(64),
        "HHB": aligned(65),
        "HHF": aligned(70),
        "LHA": aligned(57),
        "LHB": aligned(55),
        "LHF": aligned(48),
        "VocalPad1": [],
        "VocalPad2": [],
        "VocalPad3": [],
        "VocalPad4": [],
    }
    for time, duration, pitches in pad_blocks or []:
        for track_name, midi in zip(validate_bv_music.PAD_TRACKS, pitches):
            result[track_name].append(
                {
                    "name": validate_bv_music.midi_name(midi),
                    "midi": midi,
                    "time": time,
                    "duration": duration,
                }
            )
    return result


GOOD_PAD_BLOCKS = [
    (8, 2, (70, 64, 57, 48)),
    (10, 2, (69, 65, 58, 52)),
]


def midi_sequence(midis: list[int], duration: float = 1) -> list[dict[str, Any]]:
    return [
        {
            "name": validate_bv_music.midi_name(midi),
            "midi": midi,
            "time": index * duration,
            "duration": duration,
        }
        for index, midi in enumerate(midis)
    ]


class ValidateBvMusicTest(unittest.TestCase):
    def write_project(self, root: Path, payload_tracks: dict[str, list[dict[str, Any]]]) -> Path:
        (root / "melody.json").write_text(
            json.dumps({"header": header(), "melody": melody()}),
            encoding="utf-8",
        )
        (root / "note.txt").write_text(
            "\n".join(
                [
                    "Time Unit: beats",
                    "",
                    "Bar 1: Time Signature 4/4, BPM 120",
                    "Bar 1: beat 1 C | beat 2 F",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        bv_path = root / "bv_raw.json"
        bv_path.write_text(
            json.dumps({"header": header(), "tracks": payload_tracks}),
            encoding="utf-8",
        )
        return bv_path

    def write_note_chart(self, path: Path, chord_lines: list[str], marker_lines: list[str]) -> None:
        path.write_text(
            "\n".join(["Time Unit: beats", "", *marker_lines, *chord_lines]) + "\n",
            encoding="utf-8",
        )

    def write_sectional_project(
        self,
        root: Path,
        payload_tracks: dict[str, list[dict[str, Any]]],
    ) -> Path:
        chart_header = sectional_header()
        (root / "melody.json").write_text(
            json.dumps(
                {
                    "header": chart_header,
                    "melody": sectional_melody(),
                    "control": [
                        {"section": "verse", "time": 0, "duration": 8},
                        {"section": "chorus", "time": 8, "duration": 8},
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.write_note_chart(
            root / "note.txt",
            [f"Bar {bar}: beat 1 C13" for bar in range(1, 5)],
            ["Bar 1: Time Signature 4/4, BPM 120"],
        )
        bv_path = root / "bv_raw.json"
        bv_path.write_text(
            json.dumps({"header": chart_header, "tracks": payload_tracks}),
            encoding="utf-8",
        )
        return bv_path

    def test_passes_valid_arrangement_report(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bv_path = self.write_project(Path(temp_dir), tracks())

            report, exit_code = validate_bv_music.validate(
                bv_path, min_score=80, pad_policy="optional"
            )

        self.assertEqual(exit_code, 0)
        self.assertTrue(report["passed"])
        self.assertGreaterEqual(report["score"], 80)
        self.assertEqual(report["hardErrors"], [])
        self.assertEqual(report["policySource"], "cli")
        self.assertFalse(report["planDetected"])
        self.assertIsNone(report["planFingerprintVerified"])
        self.assertIsNone(report["rawMatchesPlan"])
        self.assertEqual(report["deliveryBlockers"], [])
        self.assertIn(report["workflowStatus"], {"ready", "reviewRecommended"})

    def test_score_v5_report_and_good_sectional_pad_pass(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bv_path = self.write_sectional_project(
                Path(temp_dir), sectional_tracks(GOOD_PAD_BLOCKS)
            )

            report, exit_code = validate_bv_music.validate(bv_path, min_score=80)

        self.assertEqual(exit_code, 0)
        self.assertTrue(report["passed"])
        self.assertEqual(report["scoreVersion"], "bv-score-v5")
        self.assertEqual(report["padPolicy"], "sectional")
        self.assertEqual(report["policySource"], "default")
        self.assertFalse(report["planDetected"])
        self.assertIsNone(report["rawMatchesPlan"])
        self.assertTrue(report["deliveryEligible"])
        self.assertTrue(report["scoreThresholdMet"])
        self.assertEqual(
            set(report["categoryScores"]),
            {
                "harmonyShape",
                "padPresence",
                "padWriting",
                "harmonicFit",
                "structureAndPlayability",
            },
        )
        self.assertEqual(
            set(report["qualityAxes"]),
            {"harmonicCorrectness", "musicalStructure"},
        )
        self.assertEqual(report["qualityAxisFailures"], [])
        self.assertTrue(report["qualityAxes"]["harmonicCorrectness"]["passedFloor"])
        self.assertTrue(report["qualityAxes"]["musicalStructure"]["passedFloor"])
        self.assertEqual(
            report["metrics"]["padPresence"]["sections"]["chorus"][
                "effectiveCoverageRate"
            ],
            0.5,
        )
        self.assertEqual(report["floorFailures"], [])
        self.assertFalse(report["metrics"]["harmonyShape"]["limitedEvidence"])
        self.assertEqual(
            report["metrics"]["harmonyShape"]["availableTransitionEvidence"], 7
        )

    def test_persisted_score_fixture_passes_v5_default_delivery_gate(self) -> None:
        fixture = ROOT / "test" / "fixtures" / "score_v3_valid"

        report, exit_code = validate_bv_music.validate(fixture, min_score=80)

        self.assertEqual(exit_code, 0)
        self.assertTrue(report["passed"])
        self.assertGreaterEqual(report["score"], 80)
        self.assertEqual(report["musicalGateFailures"], [])
        self.assertEqual(report["floorFailures"], [])
        self.assertEqual(report["policySource"], "plan")
        self.assertTrue(report["planDetected"])
        self.assertTrue(report["planFingerprintVerified"])
        self.assertTrue(report["rawMatchesPlan"])

    def test_category_floor_cannot_be_bypassed_by_lowering_min_score(self) -> None:
        payload_tracks = sectional_tracks()
        base_voicing = (72, 65, 62, 48)
        shifts = (0, 2, 5, 0, 2, 5, 0, 2)
        for time, shift in zip(range(8, 16), shifts):
            for track_name, base_midi in zip(
                validate_bv_music.PAD_TRACKS, base_voicing
            ):
                midi = base_midi + shift
                payload_tracks[track_name].append(
                    {
                        "name": validate_bv_music.midi_name(midi),
                        "midi": midi,
                        "time": time,
                        "duration": 1,
                    }
                )

        with TemporaryDirectory() as temp_dir:
            bv_path = self.write_sectional_project(Path(temp_dir), payload_tracks)
            report, exit_code = validate_bv_music.validate(bv_path, min_score=0)

        self.assertEqual(report["musicalGateFailures"], [])
        self.assertIn("padWriting", report["floorFailures"])
        self.assertFalse(report["categoryScores"]["padWriting"]["passedFloor"])
        self.assertFalse(report["passed"])
        self.assertEqual(exit_code, 1)
        self.assertEqual(report["workflowStatus"], "blocked")
        self.assertTrue(
            any(
                blocker["kind"] == "categoryFloor"
                and blocker["category"] == "padWriting"
                for blocker in report["deliveryBlockers"]
            )
        )

    def test_empty_chorus_pad_fails_and_scores_below_good_pad(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            good_root = root / "good"
            empty_root = root / "empty"
            good_root.mkdir()
            empty_root.mkdir()
            good_path = self.write_sectional_project(
                good_root, sectional_tracks(GOOD_PAD_BLOCKS)
            )
            empty_path = self.write_sectional_project(empty_root, sectional_tracks())

            good_report, good_exit = validate_bv_music.validate(good_path, min_score=80)
            empty_report, empty_exit = validate_bv_music.validate(empty_path, min_score=80)

        self.assertEqual(good_exit, 0)
        self.assertEqual(empty_exit, 1)
        self.assertGreaterEqual(good_report["score"] - empty_report["score"], 10)
        self.assertIn("padPresence", empty_report["floorFailures"])
        self.assertEqual(
            empty_report["categoryScores"]["padWriting"]["status"],
            "notEvaluable",
        )

    def test_optional_policy_explicitly_omits_empty_pad_presence(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bv_path = self.write_sectional_project(Path(temp_dir), sectional_tracks())

            report, exit_code = validate_bv_music.validate(
                bv_path, min_score=80, pad_policy="optional"
            )

        self.assertEqual(exit_code, 0)
        self.assertTrue(report["passed"])
        self.assertEqual(report["padPolicy"], "optional")
        self.assertFalse(report["categoryScores"]["padPresence"]["applicable"])
        self.assertEqual(report["categoryScores"]["padPresence"]["status"], "omitted")

    def test_one_or_three_isolated_pad_entries_fail_chorus_presence(self) -> None:
        cases = [
            [(8, 0.5, (70, 64, 57, 48))],
            [
                (8, 0.5, (70, 64, 57, 48)),
                (9, 0.5, (69, 65, 58, 52)),
                (10, 0.5, (70, 64, 57, 50)),
            ],
        ]
        for pad_blocks in cases:
            with self.subTest(entries=len(pad_blocks)), TemporaryDirectory() as temp_dir:
                bv_path = self.write_sectional_project(
                    Path(temp_dir), sectional_tracks(pad_blocks)
                )

                report, exit_code = validate_bv_music.validate(bv_path, min_score=80)

            self.assertEqual(exit_code, 1)
            self.assertLessEqual(report["score"], validate_bv_music.DELIVERY_FLOOR_CAP)
            self.assertIn("padPresence", report["floorFailures"])
            self.assertNotIn("padWriting", report["floorFailures"])
            self.assertEqual(report["categoryScores"]["padWriting"]["score"], 0)
            self.assertFalse(report["categoryScores"]["padWriting"]["applicable"])
            self.assertEqual(
                report["categoryScores"]["padWriting"]["status"],
                "notEvaluable",
            )
            self.assertTrue(
                any("target minimum" in warning for warning in report["warnings"])
            )

    def test_partial_optional_pad_is_not_treated_as_omitted(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bv_path = self.write_sectional_project(
                Path(temp_dir),
                sectional_tracks([(8, 0.5, (70, 64, 57, 48))]),
            )

            report, exit_code = validate_bv_music.validate(
                bv_path, min_score=80, pad_policy="optional"
            )

        self.assertEqual(exit_code, 1)
        self.assertFalse(report["passed"])
        self.assertEqual(
            report["categoryScores"]["padWriting"]["status"],
            "notEvaluable",
        )
        self.assertFalse(report["categoryScores"]["padWriting"]["applicable"])
        self.assertEqual(report["categoryScores"]["padWriting"]["score"], 0)
        self.assertNotIn("padWriting", report["floorFailures"])
        self.assertTrue(
            any(
                item["code"] == "PARTIAL_OPTIONAL_PAD"
                for item in report["musicalGateFailures"]
            )
        )
        self.assertTrue(
            all(
                {"code", "observed", "required", "suggestion"} <= set(item)
                for item in report["musicalGateFailures"]
            )
        )

    def test_pad_must_support_multiple_chord_spans_when_opportunities_exist(self) -> None:
        payload_tracks = sectional_tracks([(8, 2, (70, 64, 57, 48))])
        control = [
            validate_bv_music.SectionSpan(0, 8, "verse"),
            validate_bv_music.SectionSpan(8, 16, "chorus"),
        ]
        events = [
            (time, validate_bv_music.chord_pitch_classes("C13"), "C13")
            for time in (0.0, 4.0, 8.0, 10.0, 12.0, 14.0)
        ]
        segments = validate_bv_music.build_time_signature_segments(sectional_header())
        coverage = validate_bv_music.pad_timeline_metrics(
            payload_tracks, 16, control, sectional_melody(), events, segments
        )

        category, _, _, evidence = validate_bv_music.score_pad_presence(
            coverage, "sectional", True
        )

        self.assertFalse(category["passedFloor"])
        self.assertEqual(coverage["sections"]["chorus"]["eligibleChordSpans"], 4)
        self.assertEqual(coverage["sections"]["chorus"]["supportedChordSpans"], 1)
        self.assertIn("chorus", evidence["chordSupportFailures"])
        self.assertTrue(
            any(
                item["code"] == "PAD_CHORD_SUPPORT_INSUFFICIENT"
                for item in evidence["gateFailures"]
            )
        )

    def test_sustained_four_part_pad_has_sufficient_evidence(self) -> None:
        payload_tracks = tracks()
        for track_name, midi in zip(validate_bv_music.PAD_TRACKS, (72, 67, 64, 48)):
            payload_tracks[track_name] = [
                {
                    "name": validate_bv_music.midi_name(midi),
                    "midi": midi,
                    "time": 0,
                    "duration": 4,
                }
            ]
        chart_header = {**header(), "duration": 4}
        segments = validate_bv_music.build_time_signature_segments(chart_header)
        events = [(0.0, validate_bv_music.chord_pitch_classes("C"), "C")]
        coverage = validate_bv_music.pad_timeline_metrics(
            payload_tracks, 4, [], [], events, segments
        )

        presence, _, _, evidence = validate_bv_music.score_pad_presence(
            coverage, "sectional", False
        )
        writing, _, _, _ = validate_bv_music.assess_pad_writing(
            payload_tracks, {**coverage, **evidence}
        )

        self.assertTrue(evidence["evidenceSufficient"])
        self.assertTrue(presence["passedFloor"])
        self.assertEqual(writing["status"], "scored")
        self.assertTrue(writing["passedFloor"])

    def test_complete_optional_pad_is_scored_normally(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bv_path = self.write_sectional_project(
                Path(temp_dir), sectional_tracks(GOOD_PAD_BLOCKS)
            )

            report, exit_code = validate_bv_music.validate(
                bv_path, min_score=80, pad_policy="optional"
            )

        self.assertEqual(exit_code, 0)
        self.assertTrue(report["passed"])
        self.assertEqual(report["categoryScores"]["padPresence"]["status"], "scored")
        self.assertEqual(report["musicalGateFailures"], [])

    def test_unchanged_pad_voicing_across_progression_cannot_score_full_writing(self) -> None:
        payload_tracks = tracks()
        for track_name, midi in zip(validate_bv_music.PAD_TRACKS, (72, 67, 64, 48)):
            payload_tracks[track_name] = [
                {
                    "name": validate_bv_music.midi_name(midi),
                    "midi": midi,
                    "time": 0,
                    "duration": 4,
                }
            ]
        events = [
            (0.0, validate_bv_music.chord_pitch_classes("C"), "C"),
            (1.0, validate_bv_music.chord_pitch_classes("Cmaj7"), "Cmaj7"),
            (2.0, validate_bv_music.chord_pitch_classes("C6"), "C6"),
            (3.0, validate_bv_music.chord_pitch_classes("Cadd9"), "Cadd9"),
        ]
        segments = validate_bv_music.build_time_signature_segments(
            {**header(), "duration": 4}
        )
        coverage = validate_bv_music.pad_timeline_metrics(
            payload_tracks, 4, [], [], events, segments
        )

        category, warnings, _, metrics = validate_bv_music.assess_pad_writing(
            payload_tracks, coverage
        )

        self.assertEqual(metrics["evaluatedChordTransitions"], 3)
        self.assertEqual(metrics["longestUnchangedVoicingRun"], 3)
        self.assertLess(category["score"], category["maxScore"])
        self.assertTrue(any("unchanged voicing run" in item for item in warnings))

    def test_unresponsive_invalid_pad_voicing_fails_writing_floor(self) -> None:
        payload_tracks = tracks()
        for track_name, midi in zip(validate_bv_music.PAD_TRACKS, (72, 67, 64, 48)):
            payload_tracks[track_name] = [
                {
                    "name": validate_bv_music.midi_name(midi),
                    "midi": midi,
                    "time": 0,
                    "duration": 4,
                }
            ]
        events = [
            (0.0, validate_bv_music.chord_pitch_classes("C"), "C"),
            (1.0, validate_bv_music.chord_pitch_classes("F"), "F"),
            (2.0, validate_bv_music.chord_pitch_classes("G"), "G"),
            (3.0, validate_bv_music.chord_pitch_classes("Dm"), "Dm"),
        ]
        segments = validate_bv_music.build_time_signature_segments(
            {**header(), "duration": 4}
        )
        coverage = validate_bv_music.pad_timeline_metrics(
            payload_tracks, 4, [], [], events, segments
        )

        category, _, _, metrics = validate_bv_music.assess_pad_writing(
            payload_tracks, coverage
        )

        self.assertEqual(metrics["harmonicChangeResponseRate"], 0)
        self.assertFalse(category["passedFloor"])
        self.assertTrue(
            any(
                item["code"] == "PAD_HARMONIC_RESPONSE_FAILED"
                for item in metrics["gateFailures"]
            )
        )

    def test_inner_voice_changes_satisfy_pad_harmonic_response(self) -> None:
        payload_tracks = tracks()
        blocks = [
            (0, (72, 67, 64, 48)),
            (1, (72, 69, 65, 53)),
            (2, (71, 67, 62, 55)),
            (3, (72, 67, 64, 48)),
        ]
        for time, pitches in blocks:
            for track_name, midi in zip(validate_bv_music.PAD_TRACKS, pitches):
                payload_tracks[track_name].append(
                    {
                        "name": validate_bv_music.midi_name(midi),
                        "midi": midi,
                        "time": time,
                        "duration": 1,
                    }
                )
        events = [
            (0.0, validate_bv_music.chord_pitch_classes("C"), "C"),
            (1.0, validate_bv_music.chord_pitch_classes("F"), "F"),
            (2.0, validate_bv_music.chord_pitch_classes("G"), "G"),
            (3.0, validate_bv_music.chord_pitch_classes("C"), "C"),
        ]
        segments = validate_bv_music.build_time_signature_segments(
            {**header(), "duration": 4}
        )
        coverage = validate_bv_music.pad_timeline_metrics(
            payload_tracks, 4, [], [], events, segments
        )

        category, warnings, _, metrics = validate_bv_music.assess_pad_writing(
            payload_tracks, coverage
        )

        self.assertEqual(metrics["evaluatedChordTransitions"], 3)
        self.assertEqual(metrics["harmonicChangeResponseRate"], 1)
        self.assertTrue(category["passedFloor"])
        self.assertFalse(any("chord-change response" in item for item in warnings))

    def test_six_and_seven_repeated_pad_notes_are_scored(self) -> None:
        for note_count in (6, 7):
            with self.subTest(note_count=note_count):
                payload_tracks = tracks()
                for track_index, track_name in enumerate(validate_bv_music.PAD_TRACKS):
                    midi = 72 - track_index * 7
                    payload_tracks[track_name] = [
                        {
                            "name": validate_bv_music.midi_name(midi),
                            "midi": midi,
                            "time": index,
                            "duration": 1,
                        }
                        for index in range(note_count)
                    ]

                coverage = validate_bv_music.pad_timeline_metrics(
                    payload_tracks,
                    note_count,
                    [], [], [], validate_bv_music.build_time_signature_segments(
                        {**header(), "duration": note_count}
                    ),
                )
                _, warnings, _, _ = validate_bv_music.assess_pad_writing(payload_tracks, coverage)

                self.assertTrue(
                    any("prolonged pitch repetition" in warning for warning in warnings)
                )

    def test_duration_weighted_pad_coverage_uses_interval_union(self) -> None:
        payload_tracks = tracks()
        payload_tracks["VocalPad1"] = [
            {"name": "C5", "midi": 72, "time": 0, "duration": 2}
        ]
        payload_tracks["VocalPad2"] = [
            {"name": "G4", "midi": 67, "time": 1, "duration": 2}
        ]
        chart_header = {**header(), "duration": 4}
        segments = validate_bv_music.build_time_signature_segments(chart_header)

        metrics = validate_bv_music.pad_timeline_metrics(
            payload_tracks, 4, [], [], [], segments
        )

        self.assertEqual(metrics["activeBeats"], 3)
        self.assertEqual(metrics["fewerThanThreeBeats"], 3)
        self.assertEqual(metrics["trackCoverageBeats"]["VocalPad1"], 2)
        self.assertEqual(metrics["trackCoverageBeats"]["VocalPad2"], 2)
        self.assertEqual(metrics["trackCoverageRates"]["VocalPad1"], 0.5)
        self.assertEqual(metrics["trackCoverageRates"]["VocalPad2"], 0.5)
        self.assertEqual(metrics["sections"]["default"]["atLeastOneBeats"], 3)

        _, _, _, presence_metrics = validate_bv_music.score_pad_presence(
            metrics, "sectional", False
        )
        self.assertEqual(presence_metrics["sectionPolicySource"], "default")

    def test_rigid_high_coverage_pad_misses_pad_writing_floor(self) -> None:
        payload_tracks = tracks()
        bases = {"VocalPad1": 72, "VocalPad2": 67, "VocalPad3": 60, "VocalPad4": 48}
        for track_name, base in bases.items():
            payload_tracks[track_name] = [
                {
                    "name": validate_bv_music.midi_name(base + index * 2),
                    "midi": base + index * 2,
                    "time": index,
                    "duration": 1,
                }
                for index in range(4)
            ]
        chart_header = {**header(), "duration": 4}
        segments = validate_bv_music.build_time_signature_segments(chart_header)
        coverage = validate_bv_music.pad_timeline_metrics(
            payload_tracks, 4, [], [], [], segments
        )

        category, warnings, _, _ = validate_bv_music.assess_pad_writing(
            payload_tracks, coverage
        )

        self.assertFalse(category["passedFloor"])
        self.assertTrue(any("rigid parallel blocks" in warning for warning in warnings))

    def test_optional_policy_still_reports_pad_inside_no_bv(self) -> None:
        payload_tracks = tracks()
        for track_name, midi in zip(
            validate_bv_music.PAD_TRACKS, (72, 67, 60, 48)
        ):
            payload_tracks[track_name] = [
                {
                    "name": validate_bv_music.midi_name(midi),
                    "midi": midi,
                    "time": 0,
                    "duration": 1,
                }
            ]
        segments = validate_bv_music.build_time_signature_segments(header())
        coverage = validate_bv_music.pad_timeline_metrics(
            payload_tracks,
            2,
            [validate_bv_music.SectionSpan(0, 2, "no_bv")],
            [],
            [],
            segments,
        )

        _, warnings, _, _ = validate_bv_music.score_pad_presence(
            coverage, "optional", True
        )

        self.assertTrue(any("inside no_bv" in warning for warning in warnings))

    def test_sectional_policy_allows_empty_verse_only_pad(self) -> None:
        payload_tracks = tracks()
        segments = validate_bv_music.build_time_signature_segments(header())
        coverage = validate_bv_music.pad_timeline_metrics(
            payload_tracks,
            2,
            [validate_bv_music.SectionSpan(0, 2, "verse")],
            [],
            [],
            segments,
        )

        category, warnings, _, metrics = validate_bv_music.score_pad_presence(
            coverage, "sectional", True
        )

        self.assertTrue(category["passedFloor"])
        self.assertEqual(category["score"], category["maxScore"])
        self.assertEqual(metrics["requiredSectionFailures"], [])
        self.assertEqual(warnings, [])

    def test_required_policy_requires_pad_in_verse(self) -> None:
        payload_tracks = sectional_tracks(GOOD_PAD_BLOCKS)
        segments = validate_bv_music.build_time_signature_segments(sectional_header())
        control = [
            validate_bv_music.SectionSpan(0, 8, "verse"),
            validate_bv_music.SectionSpan(8, 16, "chorus"),
        ]
        coverage = validate_bv_music.pad_timeline_metrics(
            payload_tracks, 16, control, sectional_melody(), [], segments
        )

        category, _, _, metrics = validate_bv_music.score_pad_presence(
            coverage, "required", True
        )

        self.assertFalse(category["passedFloor"])
        self.assertIn("verse", metrics["requiredSectionFailures"])

    def test_pad_presence_uses_bounded_target_bands(self) -> None:
        in_band = validate_bv_music.target_band_score(0.3, 0.0, 0.45)
        overfull = validate_bv_music.target_band_score(1.0, 0.0, 0.45)

        self.assertEqual(in_band, 1.0)
        self.assertLess(overfull, in_band)

    def test_rejects_invalid_pad_policy(self) -> None:
        report, exit_code = validate_bv_music.validate(
            Path("unused"), min_score=80, pad_policy="wrong"
        )

        self.assertEqual(exit_code, 1)
        self.assertIn("pad_policy must be one of", report["hardErrors"][0])
        self.assertEqual(report["workflowStatus"], "blocked")
        self.assertEqual(report["deliveryBlockers"][0]["kind"], "hardError")

    def test_score_threshold_is_a_blocker_only_after_delivery_gates_pass(self) -> None:
        payload_tracks = sectional_tracks(GOOD_PAD_BLOCKS)
        payload_tracks["HHA"][0]["midi"] = 76
        payload_tracks["HHA"][0]["name"] = "E5"
        with TemporaryDirectory() as temp_dir:
            bv_path = self.write_sectional_project(Path(temp_dir), payload_tracks)
            report, exit_code = validate_bv_music.validate(bv_path, min_score=100)

        self.assertEqual(exit_code, 1)
        self.assertTrue(report["deliveryEligible"])
        self.assertFalse(report["scoreThresholdMet"])
        self.assertEqual(report["workflowStatus"], "blocked")
        self.assertEqual(
            [blocker["code"] for blocker in report["deliveryBlockers"]],
            ["SCORE_THRESHOLD_NOT_MET"],
        )

    def test_parse_args_accepts_pad_policy(self) -> None:
        args = validate_bv_music.parse_args(["demo", "--pad-policy", "required"])

        self.assertEqual(args.pad_policy, "required")

    def test_parse_args_leaves_policy_implicit_by_default(self) -> None:
        args = validate_bv_music.parse_args(["demo"])

        self.assertIsNone(args.pad_policy)
        self.assertFalse(args.allow_policy_override)

    def test_policy_override_requires_an_explicit_policy(self) -> None:
        report, exit_code = validate_bv_music.validate(
            Path("unused"), min_score=80, allow_policy_override=True
        )

        self.assertEqual(exit_code, 1)
        self.assertFalse(report["passed"])
        self.assertIn(
            "requires an explicit pad_policy", report["hardErrors"][0]
        )

    def test_all_harmony_pairs_are_reported_and_long_hhf_copy_fails_floor(self) -> None:
        fixture = SPRINT2_FIXTURE["harmonyIndependence"]
        lead = midi_sequence(fixture["lead"])
        payload_tracks = tracks()
        payload_tracks["HHA"] = midi_sequence(fixture["expressive"])
        payload_tracks["HHB"] = midi_sequence([midi + 3 for midi in fixture["expressive"]])
        payload_tracks["HHF"] = [dict(note) for note in payload_tracks["HHA"]]
        payload_tracks["LHA"] = midi_sequence([midi - 12 for midi in fixture["expressive"]])
        payload_tracks["LHB"] = midi_sequence([midi - 15 for midi in fixture["expressive"]])
        payload_tracks["LHF"] = midi_sequence([midi - 19 for midi in fixture["expressive"]])
        segments = validate_bv_music.build_time_signature_segments(
            {**header(), "duration": len(lead)}
        )

        category, warnings, _, metrics = validate_bv_music.analyze_harmony_shape(
            payload_tracks, lead, [], segments
        )

        self.assertEqual(len(metrics["pairs"]), 6)
        duplicate = next(item for item in metrics["pairs"] if item["pair"] == "HHA:HHF")
        self.assertTrue(duplicate["majorDuplication"])
        self.assertEqual(duplicate["longestExactRunNotes"], len(lead))
        self.assertFalse(category["passedFloor"])
        self.assertTrue(any("HHA:HHF" in warning for warning in warnings))

    def test_long_lhf_copy_is_detected_but_short_common_tone_is_not_major(self) -> None:
        fixture = SPRINT2_FIXTURE["harmonyIndependence"]
        lead = midi_sequence(fixture["lead"])
        payload_tracks = tracks()
        for track_name, offset in (
            ("HHA", 4), ("HHB", 7), ("HHF", 11),
            ("LHA", -4), ("LHB", -7), ("LHF", -4),
        ):
            payload_tracks[track_name] = midi_sequence([midi + offset for midi in fixture["lead"]])
        segments = validate_bv_music.build_time_signature_segments(
            {**header(), "duration": len(lead)}
        )

        category, _, _, metrics = validate_bv_music.analyze_harmony_shape(
            payload_tracks, lead, [], segments
        )
        duplicate = next(item for item in metrics["pairs"] if item["pair"] == "LHA:LHF")
        self.assertTrue(duplicate["majorDuplication"])
        self.assertFalse(category["passedFloor"])

        short = validate_bv_music.harmony_pair_metrics(
            "HHA",
            "HHF",
            {
                **payload_tracks,
                "HHA": midi_sequence(fixture["expressive"]),
                "HHF": midi_sequence(fixture["shortCommonOnly"]),
            },
            segments,
        )
        self.assertEqual(short["longestExactRunNotes"], 1)
        self.assertFalse(short["majorDuplication"])

    def test_phrase_level_two_note_template_is_reported(self) -> None:
        lead = midi_sequence([60, 62, 64, 65, 67, 69, 71, 72])
        payload_tracks = tracks()
        payload_tracks["HHA"] = midi_sequence([64, 67, 64, 67, 64, 67, 64, 67])
        for track_name, offset in (
            ("HHB", 7), ("HHF", 11), ("LHA", -4), ("LHB", -7), ("LHF", -11)
        ):
            payload_tracks[track_name] = midi_sequence(
                [int(note["midi"]) + offset for note in lead]
            )
        segments = validate_bv_music.build_time_signature_segments(
            {**header(), "duration": len(lead)}
        )

        _, warnings, _, metrics = validate_bv_music.analyze_harmony_shape(
            payload_tracks, lead, [], segments
        )

        self.assertEqual(metrics["contours"]["HHA"]["phraseCount"], 1)
        self.assertEqual(metrics["contours"]["HHA"]["twoNoteTemplateRate"], 1)
        self.assertTrue(any("HHA has phrase-level contour risk" in item for item in warnings))

    def test_fixed_transposition_without_unisons_fails_harmony_floor(self) -> None:
        fixture = SPRINT2_FIXTURE["harmonyIndependence"]
        lead = midi_sequence(fixture["lead"])
        payload_tracks = tracks()
        for track_name, offset in (
            ("HHA", 4), ("HHB", 7), ("HHF", 11),
            ("LHA", -4), ("LHB", -7), ("LHF", -11),
        ):
            payload_tracks[track_name] = midi_sequence(
                [midi + offset for midi in fixture["lead"]]
            )
        segments = validate_bv_music.build_time_signature_segments(
            {**header(), "duration": len(lead)}
        )

        category, warnings, _, metrics = validate_bv_music.analyze_harmony_shape(
            payload_tracks, lead, [], segments
        )

        self.assertFalse(category["passedFloor"])
        self.assertTrue(
            any(item["code"] == "MECHANICAL_PARALLEL_COPY" for item in metrics["gateFailures"])
        )
        self.assertTrue(
            any(item["code"] == "MECHANICAL_PARALLEL_GROUP" for item in metrics["gateFailures"])
        )
        self.assertTrue(any("mechanically copies LeadDouble" in item for item in warnings))
        self.assertTrue(
            all(float(pair["exactMidiDuplicationRate"]) == 0 for pair in metrics["pairs"])
        )

    def test_short_source_gaps_do_not_reset_fixed_transposition_evidence(self) -> None:
        lead_midis = [60, 62, 64, 65, 67, 69, 67, 65]

        def spaced_notes(midis: list[int]) -> list[dict[str, Any]]:
            return [
                {
                    "name": validate_bv_music.midi_name(midi),
                    "midi": midi,
                    "time": index,
                    "duration": 0.5,
                }
                for index, midi in enumerate(midis)
            ]

        lead = spaced_notes(lead_midis)
        harmony = spaced_notes([midi + 4 for midi in lead_midis])

        metrics = validate_bv_music.transition_copy_metrics(harmony, lead)

        self.assertEqual(metrics["validTransitions"], 7)
        self.assertEqual(metrics["comparableTransitions"], 7)
        self.assertEqual(metrics["exactDeltaCopyRate"], 1)
        self.assertEqual(metrics["longestRigidRunTransitions"], 7)
        self.assertTrue(metrics["mechanicalParallel"])

    def test_two_beat_musical_break_resets_fixed_transposition_run(self) -> None:
        times = [0, 1, 2, 3, 5.5, 6.5, 7.5, 8.5]
        lead_midis = [60, 62, 64, 65, 67, 69, 67, 65]

        def notes(midis: list[int]) -> list[dict[str, Any]]:
            return [
                {
                    "name": validate_bv_music.midi_name(midi),
                    "midi": midi,
                    "time": time,
                    "duration": 0.5,
                }
                for time, midi in zip(times, midis)
            ]

        lead = notes(lead_midis)
        harmony = notes([midi + 4 for midi in lead_midis])

        metrics = validate_bv_music.transition_copy_metrics(harmony, lead)

        self.assertEqual(metrics["validTransitions"], 6)
        self.assertEqual(metrics["longestRigidRunTransitions"], 3)
        self.assertFalse(metrics["mechanicalParallel"])

    def test_flat_voice_role_rewards_stability_and_rejects_contour_copy(self) -> None:
        fixture = SPRINT2_FIXTURE["harmonyIndependence"]
        lead = midi_sequence(fixture["lead"])
        payload_tracks = tracks()
        payload_tracks["HHA"] = midi_sequence(fixture["expressive"])
        payload_tracks["HHB"] = midi_sequence(
            [midi + 3 for midi in fixture["expressive"]]
        )
        payload_tracks["LHA"] = midi_sequence(
            [midi - 12 for midi in fixture["expressive"]]
        )
        payload_tracks["LHB"] = midi_sequence(
            [midi - 15 for midi in fixture["expressive"]]
        )
        payload_tracks["HHF"] = midi_sequence(fixture["distinctFlat"])
        payload_tracks["LHF"] = midi_sequence(
            [midi - 24 for midi in fixture["distinctFlat"]]
        )

        stable_score, _, _, stable_metrics, stable_gates = (
            validate_bv_music.analyze_flat_voice_roles(
                payload_tracks, lead, [], None
            )
        )
        payload_tracks["HHF"] = [dict(note) for note in payload_tracks["HHA"]]
        copy_score, warnings, _, copy_metrics, copy_gates = (
            validate_bv_music.analyze_flat_voice_roles(
                payload_tracks, lead, [], None
            )
        )

        self.assertGreater(stable_score, copy_score)
        self.assertEqual(stable_gates, [])
        self.assertTrue(
            any(item["code"] == "FLAT_VOICE_CONTOUR_COPY" for item in copy_gates)
        )
        self.assertEqual(
            copy_metrics["perTrack"]["HHF"]["maximumContourCopyRate"], 1
        )
        self.assertTrue(any("stable voice" in item for item in warnings))

    def test_sustained_legal_chord_tone_cluster_fails_vertical_voicing(self) -> None:
        payload_tracks = sectional_tracks(GOOD_PAD_BLOCKS)
        clustered = {
            "HHA": 62,
            "HHB": 64,
            "HHF": 65,
            "LHA": 58,
            "LHB": 57,
            "LHF": 55,
        }
        for track_name, midi in clustered.items():
            for note in payload_tracks[track_name]:
                note["midi"] = midi
                note["name"] = validate_bv_music.midi_name(midi)
        lead = payload_tracks["LeadDouble"]
        segments = validate_bv_music.build_time_signature_segments(sectional_header())

        category, warnings, _, metrics = validate_bv_music.analyze_harmony_shape(
            payload_tracks, lead, [], segments
        )

        self.assertFalse(category["passedFloor"])
        self.assertEqual(metrics["verticalVoicing"]["denseClusterRate"], 1)
        self.assertTrue(
            any(
                item["code"] == "VERTICAL_VOICING_CLUSTERED"
                for item in metrics["gateFailures"]
            )
        )
        self.assertTrue(any("step clusters" in item for item in warnings))

    def test_chord_correct_cluster_fails_musical_structure_axis_end_to_end(self) -> None:
        payload_tracks = sectional_tracks(GOOD_PAD_BLOCKS)
        clustered = {
            "HHA": 62,
            "HHB": 64,
            "HHF": 65,
            "LHA": 58,
            "LHB": 57,
            "LHF": 55,
        }
        for track_name, midi in clustered.items():
            for note in payload_tracks[track_name]:
                note["midi"] = midi
                note["name"] = validate_bv_music.midi_name(midi)

        with TemporaryDirectory() as temp_dir:
            bv_path = self.write_sectional_project(Path(temp_dir), payload_tracks)
            report, exit_code = validate_bv_music.validate(bv_path, min_score=80)

        self.assertEqual(exit_code, 1)
        self.assertEqual(report["qualityAxes"]["harmonicCorrectness"]["score"], 100)
        self.assertTrue(report["qualityAxes"]["harmonicCorrectness"]["passedFloor"])
        self.assertEqual(report["qualityAxes"]["musicalStructure"]["score"], 79)
        self.assertFalse(report["qualityAxes"]["musicalStructure"]["passedFloor"])
        self.assertIn("musicalStructure", report["qualityAxisFailures"])
        self.assertTrue(
            any(
                blocker["kind"] == "qualityAxisFloor"
                and blocker["axis"] == "musicalStructure"
                for blocker in report["deliveryBlockers"]
            )
        )

    def test_quality_axes_prevent_correctness_and_structure_compensation(self) -> None:
        categories = {
            "harmonyShape": validate_bv_music.category_result("harmonyShape", 25),
            "padPresence": validate_bv_music.category_result(
                "padPresence", 0, floor=0, applicable=False, status="omitted"
            ),
            "padWriting": validate_bv_music.category_result(
                "padWriting", 0, floor=0, applicable=False, status="notEvaluable"
            ),
            "harmonicFit": validate_bv_music.category_result("harmonicFit", 15),
            "structureAndPlayability": validate_bv_music.category_result(
                "structureAndPlayability", 15
            ),
        }

        score, raw, floor_failures, axes, axis_failures = (
            validate_bv_music.score_quality_axes(categories)
        )

        self.assertEqual(floor_failures, [])
        self.assertEqual(axes["harmonicCorrectness"]["score"], 75)
        self.assertEqual(axes["musicalStructure"]["score"], 100)
        self.assertEqual(axis_failures, ["harmonicCorrectness"])
        self.assertEqual(raw, 75)
        self.assertEqual(score, 75)

    def test_short_parallel_passage_is_penalized_but_not_severe(self) -> None:
        lead = midi_sequence([60, 62, 64, 65, 67, 69])
        harmony = midi_sequence([64, 66, 68, 69, 68, 67])

        metrics = validate_bv_music.transition_copy_metrics(harmony, lead)

        self.assertTrue(metrics["parallelHint"])
        self.assertFalse(metrics["mechanicalParallel"])
        self.assertEqual(metrics["longestRigidRunTransitions"], 3)

    def test_long_same_direction_motion_without_equal_deltas_cannot_score_full(self) -> None:
        lead = midi_sequence([60, 62, 64, 65, 67, 69])
        harmony = midi_sequence([64, 67, 68, 70, 71, 74])

        metrics = validate_bv_music.transition_copy_metrics(harmony, lead)

        self.assertEqual(metrics["exactDeltaCopyRate"], 0)
        self.assertEqual(metrics["sameDirectionRate"], 1)
        self.assertEqual(metrics["longestSameDirectionRunTransitions"], 5)
        self.assertTrue(metrics["parallelHint"])
        self.assertFalse(metrics["mechanicalParallel"])

    def test_cross_chord_sustain_reports_duration_weighted_mismatch_span(self) -> None:
        fixture = SPRINT2_FIXTURE["crossChordSustain"]
        payload_tracks = {name: [] for name in (*validate_bv_music.MELODY_ALIGNED_TRACKS, *validate_bv_music.PAD_TRACKS)}
        payload_tracks["VocalPad1"] = [fixture["note"]]
        events = [
            (
                item["time"],
                validate_bv_music.chord_pitch_classes(item["chord"]),
                item["chord"],
            )
            for item in fixture["events"]
        ]

        category, warnings, _, metrics = validate_bv_music.analyze_harmonic_fit(
            payload_tracks, events, fixture["duration"]
        )

        self.assertEqual(metrics["fitBeats"], 1)
        self.assertEqual(metrics["nonChordBeats"], 1)
        self.assertEqual(metrics["fitRate"], 0.5)
        self.assertEqual(metrics["worstNonChordSpans"][0]["chord"], "F")
        self.assertFalse(category["passedFloor"])
        self.assertTrue(any("worst episodes" in warning for warning in warnings))

    def test_short_stepwise_tension_with_resolution_is_controlled(self) -> None:
        payload_tracks = {name: [] for name in (*validate_bv_music.MELODY_ALIGNED_TRACKS, *validate_bv_music.PAD_TRACKS)}
        payload_tracks["HHA"] = [
            {"name": "F#4", "midi": 66, "time": 0, "duration": 0.5},
            {"name": "G4", "midi": 67, "time": 0.5, "duration": 0.5},
        ]
        events = [(0.0, validate_bv_music.chord_pitch_classes("C"), "C")]

        category, warnings, _, metrics = validate_bv_music.analyze_harmonic_fit(
            payload_tracks, events, 1
        )

        self.assertEqual(metrics["nonChordBeats"], 0.5)
        self.assertEqual(metrics["controlledTensionBeats"], 0.5)
        self.assertEqual(metrics["unresolvedNonChordBeats"], 0)
        self.assertEqual(category["score"], category["maxScore"])
        self.assertEqual(warnings, [])

    def test_chord_slices_cannot_split_a_long_tension_into_short_exemptions(self) -> None:
        payload_tracks = {
            name: []
            for name in (
                *validate_bv_music.MELODY_ALIGNED_TRACKS,
                *validate_bv_music.PAD_TRACKS,
            )
        }
        payload_tracks["HHA"] = [
            {"name": "F#4", "midi": 66, "time": 0, "duration": 1},
            {"name": "G4", "midi": 67, "time": 1, "duration": 1},
        ]
        events = [
            (time, validate_bv_music.chord_pitch_classes("C"), "C")
            for time in (0.0, 0.5, 1.0)
        ]

        category, warnings, _, metrics = validate_bv_music.analyze_harmonic_fit(
            payload_tracks, events, 2
        )

        hha = metrics["perTrack"]["HHA"]
        self.assertEqual(hha["controlledTensionBeats"], 0)
        self.assertEqual(hha["unresolvedNonChordBeats"], 1)
        self.assertEqual(hha["longestNonChordEpisodeBeats"], 1)
        self.assertEqual(metrics["worstNonChordEpisodes"][0]["chordSpanCount"], 2)
        self.assertFalse(category["passedFloor"])
        self.assertTrue(
            any(item["code"] == "TRACK_HARMONIC_FIT_FAILED" for item in metrics["gateFailures"])
        )
        self.assertFalse(
            any(item["code"] == "TRACK_UNRESOLVED_SPAN_TOO_LONG" for item in metrics["gateFailures"])
        )
        self.assertTrue(any("stepwise-resolved tension" in item for item in warnings))

    def test_long_pad_episode_is_reported_without_an_absolute_span_gate(self) -> None:
        payload_tracks = {
            name: []
            for name in (
                *validate_bv_music.MELODY_ALIGNED_TRACKS,
                *validate_bv_music.PAD_TRACKS,
            )
        }
        payload_tracks["VocalPad1"] = [
            {"name": "F#4", "midi": 66, "time": 0, "duration": 1},
            {"name": "C5", "midi": 72, "time": 1, "duration": 19},
        ]
        events = [
            (time, validate_bv_music.chord_pitch_classes("C"), "C")
            for time in (0.0, 0.25, 0.5, 0.75, 1.0)
        ]

        category, warnings, _, metrics = validate_bv_music.analyze_harmonic_fit(
            payload_tracks, events, 20
        )

        pad = metrics["perTrack"]["VocalPad1"]
        self.assertEqual(pad["fitRate"], 0.95)
        self.assertEqual(pad["longestNonChordEpisodeBeats"], 1)
        self.assertEqual(metrics["worstNonChordEpisodes"][0]["chordSpanCount"], 4)
        self.assertEqual(metrics["gateFailures"], [])
        self.assertTrue(category["passedFloor"])
        self.assertTrue(any("worst episodes" in item for item in warnings))

    def test_pad_does_not_receive_short_tension_exemption(self) -> None:
        payload_tracks = {name: [] for name in (*validate_bv_music.MELODY_ALIGNED_TRACKS, *validate_bv_music.PAD_TRACKS)}
        payload_tracks["VocalPad1"] = [
            {"name": "F#4", "midi": 66, "time": 0, "duration": 0.5},
            {"name": "G4", "midi": 67, "time": 0.5, "duration": 0.5},
        ]
        events = [(0.0, validate_bv_music.chord_pitch_classes("C"), "C")]

        category, _, _, metrics = validate_bv_music.analyze_harmonic_fit(
            payload_tracks, events, 1
        )

        self.assertEqual(metrics["controlledTensionBeats"], 0)
        self.assertEqual(metrics["perTrack"]["VocalPad1"]["fitRate"], 0.5)
        self.assertFalse(category["passedFloor"])

    def test_completely_wrong_pad_cannot_be_diluted_by_long_harmony_tracks(self) -> None:
        payload_tracks = {name: [] for name in (*validate_bv_music.MELODY_ALIGNED_TRACKS, *validate_bv_music.PAD_TRACKS)}
        for track_name, midi in (
            ("HHA", 60), ("HHB", 64), ("HHF", 67),
            ("LHA", 48), ("LHB", 52), ("LHF", 55),
        ):
            payload_tracks[track_name] = [
                {
                    "name": validate_bv_music.midi_name(midi),
                    "midi": midi,
                    "time": 0,
                    "duration": 100,
                }
            ]
        payload_tracks["VocalPad1"] = [
            {"name": "F#4", "midi": 66, "time": 0, "duration": 10}
        ]
        events = [(0.0, validate_bv_music.chord_pitch_classes("C"), "C")]

        category, _, _, metrics = validate_bv_music.analyze_harmonic_fit(
            payload_tracks, events, 100
        )

        self.assertEqual(metrics["perTrack"]["VocalPad1"]["fitRate"], 0)
        self.assertLessEqual(category["score"], 11)
        self.assertFalse(category["passedFloor"])
        self.assertTrue(
            any(
                item["code"] == "TRACK_HARMONIC_FIT_FAILED"
                and item["track"] == "VocalPad1"
                for item in metrics["gateFailures"]
            )
        )

    def test_nc_overlap_is_reported_without_harmonic_penalty(self) -> None:
        payload_tracks = {name: [] for name in (*validate_bv_music.MELODY_ALIGNED_TRACKS, *validate_bv_music.PAD_TRACKS)}
        payload_tracks["VocalPad1"] = [
            {"name": "F#4", "midi": 66, "time": 0, "duration": 1}
        ]
        events = [(0.0, None, "N.C.")]

        category, warnings, _, metrics = validate_bv_music.analyze_harmonic_fit(
            payload_tracks, events, 1
        )

        self.assertEqual(metrics["ncBeats"], 1)
        self.assertEqual(metrics["evaluatedBeats"], 0)
        self.assertEqual(category["score"], 0)
        self.assertFalse(category["applicable"])
        self.assertEqual(category["status"], "notEvaluable")
        self.assertEqual(warnings, [])

    def test_root_fifth_only_pad_fails_chord_definition(self) -> None:
        payload_tracks = {name: [] for name in (*validate_bv_music.MELODY_ALIGNED_TRACKS, *validate_bv_music.PAD_TRACKS)}
        for track_name, midi in zip(validate_bv_music.PAD_TRACKS, (72, 67, 60, 48)):
            payload_tracks[track_name] = [
                {
                    "name": validate_bv_music.midi_name(midi),
                    "midi": midi,
                    "time": 0,
                    "duration": 4,
                }
            ]
        events = [(0.0, validate_bv_music.chord_pitch_classes("C"), "C")]

        category, _, _, metrics = validate_bv_music.analyze_harmonic_fit(
            payload_tracks, events, 4
        )

        self.assertEqual(metrics["padChordDefinition"]["definedChordSpanRate"], 0)
        self.assertFalse(category["passedFloor"])
        self.assertTrue(
            any(item["code"] == "PAD_CHORD_DEFINITION_FAILED" for item in metrics["gateFailures"])
        )

    def test_harmonic_fit_is_length_invariant_for_same_error_rate(self) -> None:
        def score(scale: int) -> int:
            payload_tracks = {name: [] for name in (*validate_bv_music.MELODY_ALIGNED_TRACKS, *validate_bv_music.PAD_TRACKS)}
            payload_tracks["VocalPad1"] = [
                {"name": "E4", "midi": 64, "time": 0, "duration": 2 * scale}
            ]
            events = [
                (0.0, validate_bv_music.chord_pitch_classes("C"), "C"),
                (float(scale), validate_bv_music.chord_pitch_classes("F"), "F"),
            ]
            category, _, _, _ = validate_bv_music.analyze_harmonic_fit(
                payload_tracks, events, 2 * scale
            )
            return int(category["score"])

        self.assertEqual(score(1), score(16))

    def test_validator_rejects_pad_overlap_with_no_bv_and_midi_above_c6(self) -> None:
        for case in ("no_bv", "range"):
            with self.subTest(case=case), TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                payload_tracks = tracks()
                if case == "no_bv":
                    source = {
                        "header": header(),
                        "melody": melody(),
                        "control": [{"section": "no_bv", "time": 1, "duration": 1}],
                    }
                    for track_name in validate_bv_music.MELODY_ALIGNED_TRACKS:
                        payload_tracks[track_name] = payload_tracks[track_name][:1]
                    payload_tracks["VocalPad1"] = [
                        {"name": "C4", "midi": 60, "time": 0, "duration": 1.5}
                    ]
                    expected = "overlaps no_bv"
                else:
                    source = {"header": header(), "melody": melody()}
                    payload_tracks["VocalPad1"] = [
                        {"name": "C#6", "midi": 85, "time": 0, "duration": 1}
                    ]
                    expected = "outside the allowed range"
                (root / "melody.json").write_text(json.dumps(source), encoding="utf-8")
                (root / "note.txt").write_text(
                    "Time Unit: beats\n\nBar 1: Time Signature 4/4, BPM 120\nBar 1: beat 1 C\n",
                    encoding="utf-8",
                )
                bv_path = root / "bv_raw.json"
                bv_path.write_text(
                    json.dumps({"header": header(), "tracks": payload_tracks}),
                    encoding="utf-8",
                )

                report, exit_code = validate_bv_music.validate(bv_path, min_score=0)

                self.assertEqual(exit_code, 1)
                self.assertTrue(any(expected in error for error in report["hardErrors"]))

    def test_section_development_reports_static_chorus_and_pre_chorus(self) -> None:
        coverage = {
            "sectionSpans": [
                {"section": "verse", "start": 0, "effectiveCoverageRate": 0.2},
                {
                    "section": "pre_chorus",
                    "start": 8,
                    "effectiveCoverageRate": 0.2,
                    "firstHalfEffectiveCoverageRate": 0.2,
                    "secondHalfEffectiveCoverageRate": 0.2,
                },
                {"section": "chorus", "start": 16, "effectiveCoverageRate": 0.2},
            ],
            "entryCount": 4,
            "entriesNearStructuralBoundaryRate": 0.25,
            "isolatedShortEntryCount": 1,
            "isolatedShortEntries": [17],
        }

        penalty, warnings, _, metrics = validate_bv_music.analyze_section_development(coverage)

        self.assertGreater(penalty, 0)
        self.assertEqual(metrics["chorusComparisons"][0]["coverageDelta"], 0)
        self.assertEqual(metrics["preChorusBuilds"][0]["buildDelta"], 0)
        self.assertTrue(any("static" in warning for warning in warnings))
        self.assertTrue(any("isolated short" in warning for warning in warnings))

    def test_max_length_music_analysis_stays_within_review_budget(self) -> None:
        duration = 1000
        note_count = 8000
        lead = [
            {"name": "C4", "midi": 60, "time": index / 8, "duration": 0.125}
            for index in range(note_count)
        ]
        payload_tracks = {name: [] for name in (*validate_bv_music.MELODY_ALIGNED_TRACKS, *validate_bv_music.PAD_TRACKS)}
        for track_name, midi in (
            ("HHA", 64), ("HHB", 67), ("HHF", 72),
            ("LHA", 52), ("LHB", 55), ("LHF", 48),
        ):
            payload_tracks[track_name] = [
                {
                    "name": validate_bv_music.midi_name(midi),
                    "midi": midi,
                    "time": index / 8,
                    "duration": 0.125,
                }
                for index in range(note_count)
            ]
        payload_tracks["VocalPad1"] = [
            {"name": "C5", "midi": 72, "time": index / 8, "duration": 0.125}
            for index in range(note_count)
        ]
        events = [
            (float(bar * 4), validate_bv_music.chord_pitch_classes("C"), "C")
            for bar in range(250)
        ]
        segments = validate_bv_music.build_time_signature_segments(
            {**header(), "duration": duration}
        )

        started = time.perf_counter()
        _, warnings, _, fit_metrics = validate_bv_music.analyze_harmonic_fit(
            payload_tracks, events, duration
        )
        validate_bv_music.analyze_harmony_shape(payload_tracks, lead, [], segments)
        control = [
            validate_bv_music.SectionSpan(
                bar * 4,
                (bar + 1) * 4,
                "verse" if bar % 2 == 0 else "chorus",
            )
            for bar in range(250)
        ]
        timeline = validate_bv_music.pad_timeline_metrics(
            payload_tracks, duration, control, lead, events, segments
        )
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 5.0)
        self.assertEqual(timeline["entryCount"], note_count)
        self.assertLessEqual(len(fit_metrics["worstNonChordSpans"]), 12)
        self.assertLessEqual(len(warnings), 1)

    def test_scores_duplicate_lower_harmony_pair(self) -> None:
        with TemporaryDirectory() as temp_dir:
            payload_tracks = tracks()
            payload_tracks["LHB"] = [dict(note) for note in payload_tracks["LHA"]]
            bv_path = self.write_project(Path(temp_dir), payload_tracks)

            report, _ = validate_bv_music.validate(bv_path, min_score=0)

        self.assertTrue(
            any("LHA and LHB duplicate each other" in warning for warning in report["warnings"])
        )

    def test_scores_expressive_harmony_outside_preferred_spacing(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bv_path = self.write_project(Path(temp_dir), tracks())

            report, _ = validate_bv_music.validate(bv_path, min_score=0)

        self.assertTrue(
            any(
                "LHA falls outside the preferred 2-4 scale-degree spacing" in warning
                for warning in report["warnings"]
            )
        )

    def test_bar_start_alone_is_not_a_phrase_boundary(self) -> None:
        segments = validate_bv_music.build_time_signature_segments(
            {
                **header(),
                "duration": 8,
            }
        )
        previous = {"name": "C4", "midi": 60, "time": 3, "duration": 1}
        current = {"name": "C5", "midi": 72, "time": 4, "duration": 1}

        self.assertFalse(
            validate_bv_music.is_phrase_boundary(previous, current, [])
        )

    def test_large_leap_inside_phrase_is_scored(self) -> None:
        segments = validate_bv_music.build_time_signature_segments(header())
        previous = {"name": "C4", "midi": 60, "time": 0, "duration": 1}
        current = {"name": "C5", "midi": 72, "time": 1, "duration": 1}

        self.assertFalse(
            validate_bv_music.is_phrase_boundary(previous, current, [])
        )

    def test_score_reports_large_leap_inside_phrase(self) -> None:
        with TemporaryDirectory() as temp_dir:
            payload_tracks = tracks()
            payload_tracks["HHA"][1]["midi"] = 76
            payload_tracks["HHA"][1]["name"] = "E5"
            bv_path = self.write_project(Path(temp_dir), payload_tracks)

            report, _ = validate_bv_music.validate(bv_path, min_score=0)

        self.assertTrue(
            any(
                "HHA has 1 leaps larger than a fifth away from phrase boundaries" in warning
                for warning in report["warnings"]
            )
        )

    def test_scores_rigid_parallel_pad_motion(self) -> None:
        payload_tracks = tracks()
        bases = {
            "VocalPad1": 72,
            "VocalPad2": 67,
            "VocalPad3": 60,
            "VocalPad4": 48,
        }
        for track_name, base in bases.items():
            payload_tracks[track_name] = [
                {
                    "name": validate_bv_music.midi_name(base + index * 2),
                    "midi": base + index * 2,
                    "time": index,
                    "duration": 1,
                }
                for index in range(4)
            ]

        coverage = validate_bv_music.pad_timeline_metrics(
            payload_tracks,
            4,
            [], [], [], validate_bv_music.build_time_signature_segments(
                {
                    **header(),
                    "duration": 4,
                }
            ),
        )
        _, warnings, _, _ = validate_bv_music.assess_pad_writing(payload_tracks, coverage)

        self.assertTrue(any("rigid parallel blocks" in warning for warning in warnings))

    def test_scores_large_pad_leap_inside_phrase(self) -> None:
        payload_tracks = tracks()
        payload_tracks["VocalPad1"] = [
            {"name": "C5", "midi": 72, "time": 0, "duration": 1},
            {"name": "C6", "midi": 84, "time": 1, "duration": 1},
        ]

        coverage = validate_bv_music.pad_timeline_metrics(
            payload_tracks,
            2,
            [], [], [], validate_bv_music.build_time_signature_segments(header()),
        )
        _, warnings, _, _ = validate_bv_music.assess_pad_writing(payload_tracks, coverage)

        self.assertTrue(any("large non-boundary leaps" in warning for warning in warnings))

    def test_scores_pad_crossing_and_register_issues(self) -> None:
        payload_tracks = tracks()
        pad_midis = {
            "VocalPad1": 48,
            "VocalPad2": 72,
            "VocalPad3": 55,
            "VocalPad4": 84,
        }
        for track_name, midi in pad_midis.items():
            payload_tracks[track_name] = [
                {
                    "name": validate_bv_music.midi_name(midi),
                    "midi": midi,
                    "time": 0,
                    "duration": 1,
                }
            ]

        coverage = validate_bv_music.pad_timeline_metrics(
            payload_tracks,
            1,
            [], [], [], validate_bv_music.build_time_signature_segments(header()),
        )
        _, warnings, _, _ = validate_bv_music.assess_pad_writing(payload_tracks, coverage)

        self.assertTrue(any("voice crossing" in warning for warning in warnings))
        self.assertTrue(any("preferred four-part registers" in warning for warning in warnings))

    def test_scores_wide_adjacent_pad_voice_spacing(self) -> None:
        payload_tracks = tracks()
        pad_midis = {
            "VocalPad1": 72,
            "VocalPad2": 55,
            "VocalPad3": 48,
            "VocalPad4": 36,
        }
        for track_name, midi in pad_midis.items():
            payload_tracks[track_name] = [
                {
                    "name": validate_bv_music.midi_name(midi),
                    "midi": midi,
                    "time": 0,
                    "duration": 1,
                }
            ]

        coverage = validate_bv_music.pad_timeline_metrics(
            payload_tracks,
            1,
            [], [], [], validate_bv_music.build_time_signature_segments(header()),
        )
        _, warnings, _, _ = validate_bv_music.assess_pad_writing(payload_tracks, coverage)

        self.assertTrue(any("adjacent-voice spacing is unusually wide" in warning for warning in warnings))

    def test_scores_sparse_and_repetitive_pad_texture(self) -> None:
        payload_tracks = tracks()
        payload_tracks["VocalPad1"] = [
            {
                "name": "C5",
                "midi": 72,
                "time": index,
                "duration": 1,
            }
            for index in range(8)
        ]

        coverage = validate_bv_music.pad_timeline_metrics(
            payload_tracks,
            8,
            [], [], [], validate_bv_music.build_time_signature_segments(
                {
                    **header(),
                    "duration": 8,
                }
            ),
        )
        _, warnings, _, _ = validate_bv_music.assess_pad_writing(payload_tracks, coverage)

        self.assertTrue(any("prolonged pitch repetition" in warning for warning in warnings))
        self.assertTrue(any("fewer than three active voices" in warning for warning in warnings))

    def test_scores_excessive_pad_density(self) -> None:
        payload_tracks = tracks()
        for track_index, track_name in enumerate(validate_bv_music.PAD_TRACKS):
            midi = 72 - track_index * 7
            payload_tracks[track_name] = [
                {
                    "name": validate_bv_music.midi_name(midi),
                    "midi": midi,
                    "time": index / 8,
                    "duration": 0.125,
                }
                for index in range(8)
            ]

        coverage = validate_bv_music.pad_timeline_metrics(
            payload_tracks,
            1,
            [], [], [], validate_bv_music.build_time_signature_segments(
                {
                    **header(),
                    "duration": 4,
                }
            ),
        )
        _, warnings, _, _ = validate_bv_music.assess_pad_writing(payload_tracks, coverage)

        self.assertTrue(any("density is unusually high" in warning for warning in warnings))

    def test_scores_combined_pad_arrangement_balance_risks(self) -> None:
        payload_tracks = tracks()
        pad_midis = {
            "VocalPad1": 48,
            "VocalPad2": 72,
            "VocalPad3": 55,
            "VocalPad4": 84,
        }
        for track_name, midi in pad_midis.items():
            payload_tracks[track_name] = [
                {
                    "name": validate_bv_music.midi_name(midi),
                    "midi": midi,
                    "time": index,
                    "duration": 1,
                }
                for index in range(8)
            ]

        coverage = validate_bv_music.pad_timeline_metrics(
            payload_tracks,
            8,
            [], [], [], validate_bv_music.build_time_signature_segments(
                {
                    **header(),
                    "duration": 8,
                }
            ),
        )
        _, warnings, _, _ = validate_bv_music.assess_pad_writing(payload_tracks, coverage)

        self.assertTrue(
            any("overall arrangement balance" in warning for warning in warnings)
        )

    def test_passes_uniform_octave_transposed_lead_and_harmonies(self) -> None:
        with TemporaryDirectory() as temp_dir:
            payload_tracks = tracks()
            for track_name in (
                "LeadDouble",
                "HHA",
                "HHB",
                "HHF",
                "LHA",
                "LHB",
                "LHF",
            ):
                for note in payload_tracks[track_name]:
                    note["midi"] += 12
                    note["name"] = validate_bv_music.midi_name(note["midi"])
            bv_path = self.write_project(Path(temp_dir), payload_tracks)

            report, exit_code = validate_bv_music.validate(
                bv_path, min_score=80, pad_policy="optional"
            )

        self.assertEqual(exit_code, 0)
        self.assertTrue(report["passed"])
        self.assertEqual(report["hardErrors"], [])

    def test_fails_hard_validation_for_extra_note_fields(self) -> None:
        with TemporaryDirectory() as temp_dir:
            payload_tracks = tracks()
            payload_tracks["HHA"][0]["velocity"] = 90
            bv_path = self.write_project(Path(temp_dir), payload_tracks)

            report, exit_code = validate_bv_music.validate(bv_path, min_score=80)

        self.assertEqual(exit_code, 1)
        self.assertFalse(report["passed"])
        self.assertIn("must contain only", report["hardErrors"][0])

    def test_fails_hard_validation_for_missing_inherited_lyric(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = melody()
            source[0]["lyric"] = "Hello"
            payload_tracks = tracks()
            for track_name in ("LeadDouble", "HHA", "HHB", "HHF", "LHA", "LHB", "LHF"):
                payload_tracks[track_name][0]["lyric"] = "Hello"
            bv_path = self.write_project(root, payload_tracks)
            (root / "melody.json").write_text(
                json.dumps({"header": header(), "melody": source}),
                encoding="utf-8",
            )
            del payload_tracks["HHA"][0]["lyric"]
            bv_path.write_text(
                json.dumps({"header": header(), "tracks": payload_tracks}),
                encoding="utf-8",
            )

            report, exit_code = validate_bv_music.validate(bv_path, min_score=80)

        self.assertEqual(exit_code, 1)
        self.assertIn("must inherit lyric", report["hardErrors"][0])

    def test_fails_when_note_txt_is_missing(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "melody.json").write_text(
                json.dumps({"header": header(), "melody": melody()}),
                encoding="utf-8",
            )
            bv_path = root / "bv_raw.json"
            bv_path.write_text(
                json.dumps({"header": header(), "tracks": tracks()}),
                encoding="utf-8",
            )

            report, exit_code = validate_bv_music.validate(bv_path, min_score=80)

        self.assertEqual(exit_code, 1)
        self.assertFalse(report["passed"])
        self.assertIn("Missing required note.txt", report["hardErrors"][0])

    def test_fails_when_source_melody_json_is_missing(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bv_path = root / "bv_raw.json"
            bv_path.write_text(
                json.dumps({"header": header(), "tracks": tracks()}),
                encoding="utf-8",
            )
            self.write_note_chart(
                root / "note.txt",
                ["Bar 1: beat 1 C"],
                ["Bar 1: Time Signature 4/4, BPM 120"],
            )

            report, exit_code = validate_bv_music.validate(bv_path, min_score=80)

        self.assertEqual(exit_code, 1)
        self.assertFalse(report["passed"])
        self.assertIn("Missing required source melody.json or canonical.json", report["hardErrors"][0])

    def test_fails_when_source_json_has_no_melody_list(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bv_path = self.write_project(root, tracks())
            (root / "melody.json").write_text(json.dumps({"header": header()}), encoding="utf-8")

            report, exit_code = validate_bv_music.validate(bv_path, min_score=80)

        self.assertEqual(exit_code, 1)
        self.assertFalse(report["passed"])
        self.assertIn("must contain a melody list", report["hardErrors"][0])

    def test_fails_when_source_melody_or_control_exceeds_limit(self) -> None:
        cases = [
            ("MAX_SOURCE_MELODY_NOTES", "Source melody note count"),
            ("MAX_CONTROL_EVENTS", "Control event count"),
        ]

        for limit_name, message in cases:
            with self.subTest(limit_name=limit_name), TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                bv_path = self.write_project(root, tracks())
                if limit_name == "MAX_CONTROL_EVENTS":
                    (root / "melody.json").write_text(
                        json.dumps(
                            {
                                "header": header(),
                                "melody": melody(),
                                "control": [
                                    {"section": "verse", "time": 0, "duration": 1},
                                    {"section": "chorus", "time": 1, "duration": 1},
                                ],
                            }
                        ),
                        encoding="utf-8",
                    )

                with patch.object(validate_bv_music.bv_raw_to_midi, limit_name, 1):
                    report, exit_code = validate_bv_music.validate(bv_path, min_score=80)

                self.assertEqual(exit_code, 1)
                self.assertFalse(report["passed"])
                self.assertIn(message, report["hardErrors"][0])

    def test_reports_soft_scoring_side_input_errors_without_crashing(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "melody.json").write_text(
                json.dumps(
                    {
                        "header": header(),
                        "melody": melody(),
                        "control": [{"section": "verse", "time": "bad", "duration": 1}],
                    }
                ),
                encoding="utf-8",
            )
            bv_path = root / "bv_raw.json"
            bv_path.write_text(
                json.dumps({"header": header(), "tracks": tracks()}),
                encoding="utf-8",
            )
            (root / "note.txt").write_text(
                "\n".join(
                    [
                        "Time Unit: beats",
                        "",
                        "Bar 1: Time Signature 4/4, BPM 120",
                        "Bar 1: beat 1 C | beat 2 F",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            report, exit_code = validate_bv_music.validate(bv_path, min_score=80)

        self.assertEqual(exit_code, 1)
        self.assertFalse(report["passed"])
        self.assertIn("Unable to score arrangement", report["hardErrors"][0])

    def test_bar_key_respects_time_signature_changes(self) -> None:
        segments = validate_bv_music.build_time_signature_segments(
            variable_time_signature_header()
        )

        self.assertEqual(validate_bv_music.bar_key(0, segments), 1)
        self.assertEqual(validate_bv_music.bar_key(3, segments), 2)
        self.assertEqual(validate_bv_music.bar_key(6, segments), 3)
        self.assertEqual(validate_bv_music.bar_key(9.5, segments), 3)
        self.assertEqual(validate_bv_music.bar_key(10, segments), 4)

    def test_chord_events_respects_time_signature_changes(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            note_txt = root / "note.txt"
            note_txt.write_text(
                "\n".join(
                    [
                        "Time Unit: beats",
                        "",
                        "Bar 1: Time Signature 3/4, BPM 120",
                        "Bar 1: beat 1 C",
                        "Bar 2: beat 1 G",
                        "Bar 3: Time Signature 4/4",
                        "Bar 3: beat 1 D",
                        "Bar 4: beat 1 F",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            events = validate_bv_music.chord_events(
                note_txt,
                variable_time_signature_header(),
            )

        self.assertEqual(
            [(time, chord) for time, _, chord in events],
            [(0, "C"), (3, "G"), (6, "D"), (10, "F")],
        )

    def test_chord_events_respects_tempo_changes(self) -> None:
        tempo_header = header()
        tempo_header["duration"] = 8
        tempo_header["tempoMap"] = [{"time": 0, "bpm": 120}, {"time": 4, "bpm": 96}]

        with TemporaryDirectory() as temp_dir:
            note_txt = Path(temp_dir) / "note.txt"
            self.write_note_chart(
                note_txt,
                ["Bar 1: beat 1 C", "Bar 2: beat 1 F"],
                [
                    "Bar 1: Time Signature 4/4, BPM 120",
                    "Bar 2: BPM 96",
                ],
            )

            events = validate_bv_music.chord_events(note_txt, tempo_header)

        self.assertEqual([(time, chord) for time, _, chord in events], [(0, "C"), (4, "F")])

    def test_chord_events_rejects_note_txt_larger_than_limit(self) -> None:
        with TemporaryDirectory() as temp_dir:
            note_txt = Path(temp_dir) / "note.txt"
            note_txt.write_bytes(b"x" * 17)

            with (
                patch.object(validate_bv_music, "MAX_NOTE_TXT_FILE_BYTES", 16),
                self.assertRaisesRegex(ValueError, "note.txt file size exceeds limit"),
            ):
                validate_bv_music.chord_events(note_txt, header())

    def test_read_note_text_rejects_file_that_grows_after_size_inspection(self) -> None:
        class GrowingNotePath:
            def exists(self) -> bool:
                return True

            def is_file(self) -> bool:
                return True

            def stat(self) -> Any:
                return type("Stat", (), {"st_size": 1})()

            def open(self, mode: str) -> Any:
                self.mode = mode
                return io.BytesIO(b"x" * (validate_bv_music.MAX_NOTE_TXT_FILE_BYTES + 1))

        with self.assertRaisesRegex(ValueError, "note.txt file size exceeds limit"):
            validate_bv_music.read_note_text(GrowingNotePath())

    def test_chord_events_rejects_arrangements_over_length_limits(self) -> None:
        cases = [
            (
                {
                    **header(),
                    "duration": 4 * 251,
                    "tempoMap": [{"time": 0, "bpm": 240}],
                },
                "250 bars",
            ),
            (
                {
                    **header(),
                    "duration": 4 * 249,
                    "tempoMap": [{"time": 0, "bpm": 96}],
                },
                "10 minutes",
            ),
        ]

        for chart_header, message in cases:
            with self.subTest(message=message), TemporaryDirectory() as temp_dir:
                note_txt = Path(temp_dir) / "note.txt"
                with self.assertRaisesRegex(ValueError, message):
                    validate_bv_music.chord_events(note_txt, chart_header)

    def test_active_chord_uses_logarithmic_lookup(self) -> None:
        class CountingEvents(list):
            def __init__(self, values: list[tuple[float, set[int] | None, str]]) -> None:
                super().__init__(values)
                self.lookups = 0

            def __getitem__(self, index: int) -> tuple[float, set[int] | None, str]:
                self.lookups += 1
                return super().__getitem__(index)

        events = CountingEvents([(float(index), {0, 4, 7}, "C") for index in range(4096)])

        chord = validate_bv_music.active_chord(events, 3000.5)

        self.assertEqual(chord, ({0, 4, 7}, "C"))
        self.assertLessEqual(events.lookups, 20)

    def test_section_lookup_is_logarithmic(self) -> None:
        class CountingSpans(list[Any]):
            def __init__(self, values: list[Any]) -> None:
                super().__init__(values)
                self.lookups = 0

            def __getitem__(self, index: int) -> Any:
                self.lookups += 1
                return super().__getitem__(index)

        spans = CountingSpans(
            [
                validate_bv_music.SectionSpan(float(index * 2), float(index * 2 + 1), "verse")
                for index in range(4096)
            ]
        )

        section = validate_bv_music.section_at(spans, 6000.5)

        self.assertEqual(section, "verse")
        self.assertLessEqual(spans.lookups, 20)

    def test_rejects_invalid_min_score(self) -> None:
        report, exit_code = validate_bv_music.validate(Path("unused"), min_score=-1)

        self.assertEqual(exit_code, 1)
        self.assertIn("between 0 and 100", report["hardErrors"][0])

    def test_chord_events_rejects_invalid_bar_and_beat_positions(self) -> None:
        cases = [
            ("Bar 0: beat 0 C", "bar must be at least 1"),
            ("Bar 1: beat 9 C", "beat 9 is outside bar 1"),
        ]

        for content, message in cases:
            with self.subTest(content=content), TemporaryDirectory() as temp_dir:
                note_txt = Path(temp_dir) / "note.txt"
                self.write_note_chart(
                    note_txt,
                    [content],
                    ["Bar 1: Time Signature 4/4, BPM 120"],
                )

                with self.assertRaisesRegex(ValueError, message):
                    validate_bv_music.chord_events(note_txt, header())

    def test_chord_events_rejects_unknown_chord_names(self) -> None:
        with TemporaryDirectory() as temp_dir:
            note_txt = Path(temp_dir) / "note.txt"
            self.write_note_chart(
                note_txt,
                ["Bar 1: beat 1 TotallyWrong"],
                ["Bar 1: Time Signature 4/4, BPM 120"],
            )

            with self.assertRaisesRegex(ValueError, "Unsupported chord name: TotallyWrong"):
                validate_bv_music.chord_events(note_txt, header())

    def test_chord_events_requires_complete_ordered_bars(self) -> None:
        complete_header = header()
        complete_header["duration"] = 8
        cases = [
            (["Bar 1: beat 1 C"], "complete and ordered"),
            (["Bar 1: beat 1 C", "Bar 1: beat 1 F"], "complete and ordered"),
            (["Bar 2: beat 1 F", "Bar 1: beat 1 C"], "complete and ordered"),
        ]

        for chord_lines, message in cases:
            with self.subTest(chord_lines=chord_lines), TemporaryDirectory() as temp_dir:
                note_txt = Path(temp_dir) / "note.txt"
                self.write_note_chart(
                    note_txt,
                    chord_lines,
                    ["Bar 1: Time Signature 4/4, BPM 120"],
                )

                with self.assertRaisesRegex(ValueError, message):
                    validate_bv_music.chord_events(note_txt, complete_header)

    def test_chord_events_requires_matching_header_markers(self) -> None:
        cases = [
            (
                variable_time_signature_header(),
                ["Bar 1: Time Signature 3/4, BPM 120"],
                "time signature markers",
            ),
            (
                header(),
                ["Bar 1: Time Signature 4/4, BPM 96"],
                "BPM markers",
            ),
        ]

        for chart_header, markers, message in cases:
            segments = validate_bv_music.build_time_signature_segments(chart_header)
            bar_count = validate_bv_music.expected_bar_count(chart_header, segments)
            chord_lines = [f"Bar {bar}: beat 1 C" for bar in range(1, bar_count + 1)]
            with self.subTest(message=message), TemporaryDirectory() as temp_dir:
                note_txt = Path(temp_dir) / "note.txt"
                self.write_note_chart(note_txt, chord_lines, markers)

                with self.assertRaisesRegex(ValueError, message):
                    validate_bv_music.chord_events(note_txt, chart_header)

    def test_chord_events_samples_mid_bar_tempo_changes_at_bar_starts(self) -> None:
        chart_header = header()
        chart_header["duration"] = 8
        chart_header["tempoMap"] = [
            {"time": 0, "bpm": 120},
            {"time": 2, "bpm": 96},
            {"time": 3, "bpm": 108},
        ]
        with TemporaryDirectory() as temp_dir:
            note_txt = Path(temp_dir) / "note.txt"
            self.write_note_chart(
                note_txt,
                ["Bar 1: beat 1 C", "Bar 2: beat 1 F"],
                [
                    "Bar 1: Time Signature 4/4, BPM 120",
                    "Bar 2: BPM 108",
                ],
            )

            events = validate_bv_music.chord_events(note_txt, chart_header)

        self.assertEqual(len(events), 2)

    def test_chord_pitch_classes_supports_all_declared_qualities(self) -> None:
        expected = {
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
        self.assertEqual(set(expected), set(validate_bv_music.CHORD_QUALITY_INTERVALS))

        for quality, pitch_classes in expected.items():
            with self.subTest(quality=quality):
                self.assertEqual(
                    validate_bv_music.chord_pitch_classes(f"C{quality}"),
                    pitch_classes,
                )

    def test_validate_reports_invalid_note_txt_without_crashing(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bv_path = self.write_project(root, tracks())
            self.write_note_chart(
                root / "note.txt",
                ["Bar 1: beat 1 TotallyWrong"],
                ["Bar 1: Time Signature 4/4, BPM 120"],
            )

            report, exit_code = validate_bv_music.validate(bv_path, min_score=80)

        self.assertEqual(exit_code, 1)
        self.assertFalse(report["passed"])
        self.assertIn("Unsupported chord name", report["hardErrors"][0])

    def test_validate_rejects_incomplete_note_txt(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            complete_header = header()
            complete_header["duration"] = 8
            (root / "melody.json").write_text(
                json.dumps({"header": complete_header, "melody": melody()}),
                encoding="utf-8",
            )
            bv_path = root / "bv_raw.json"
            bv_path.write_text(
                json.dumps({"header": complete_header, "tracks": tracks()}),
                encoding="utf-8",
            )
            self.write_note_chart(
                root / "note.txt",
                ["Bar 1: beat 1 C"],
                ["Bar 1: Time Signature 4/4, BPM 120"],
            )

            report, exit_code = validate_bv_music.validate(bv_path, min_score=80)

        self.assertEqual(exit_code, 1)
        self.assertFalse(report["passed"])
        self.assertIn("complete and ordered", report["hardErrors"][0])

    def test_hard_validation_rejects_note_end_after_header_duration(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            broken_tracks = tracks()
            broken_tracks["VocalPad1"] = [
                {"name": "C4", "midi": 60, "time": 1, "duration": 2}
            ]
            bv_path = self.write_project(root, broken_tracks)

            report, exit_code = validate_bv_music.validate(bv_path, min_score=80)

        self.assertEqual(exit_code, 1)
        self.assertIn("shorter than the latest BV note end", report["hardErrors"][0])


if __name__ == "__main__":
    unittest.main()
