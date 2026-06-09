from __future__ import annotations

import copy
import importlib.util
import json
import math
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "script"


def load_module(name: str, filename: str) -> Any:
    if str(SCRIPT_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPT_DIR))
    path = SCRIPT_DIR / filename
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bv_raw_to_midi = load_module("bv_raw_to_midi", "bv_raw_to_midi.py")
validate_bv_music = load_module("bv_plan_test_validator", "validate_bv_music.py")


def source_header(duration: int) -> dict[str, Any]:
    return {
        "startTime": 0,
        "duration": duration,
        "timeUnit": "beats",
        "grid": "1/32_bar",
        "tempoMap": [{"time": 0, "bpm": 120}],
        "timeSignatureMap": [{"time": 0, "numerator": 4, "denominator": 4}],
    }


class BvPlanToRawTest(unittest.TestCase):
    def write_source(
        self,
        root: Path,
        note_count: int,
        *,
        control: list[dict[str, Any]] | None = None,
        chord: str = "C13",
    ) -> list[dict[str, Any]]:
        melody = [
            {
                "name": "C4",
                "midi": 60,
                "time": index,
                "duration": 1,
                "lyric": f"L{index}",
            }
            for index in range(note_count)
        ]
        payload: dict[str, Any] = {
            "header": source_header(note_count),
            "melody": melody,
        }
        if control is not None:
            payload["control"] = control
        (root / "melody.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        bars = math.ceil(note_count / 4)
        lines = [
            "Time Unit: beats",
            "",
            "Bar 1: Time Signature 4/4, BPM 120",
            *[f"Bar {bar}: beat 1 {chord}" for bar in range(1, bars + 1)],
        ]
        (root / "note.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return melody

    def plan_payload(
        self,
        root: Path,
        *,
        pad_blocks: list[dict[str, Any]] | None = None,
        pad_policy: str = "sectional",
    ) -> dict[str, Any]:
        header, melody, control, note_text = bv_raw_to_midi.load_source_bundle(root)
        enabled = bv_raw_to_midi.harmony_enabled_melody(melody, control)
        offsets = {
            "HHA": 4,
            "HHB": 5,
            "HHF": 10,
            "LHA": -3,
            "LHB": -5,
            "LHF": -12,
        }
        return {
            "schemaVersion": "bv-plan-v1",
            "sourceFingerprint": bv_raw_to_midi.source_fingerprint(
                header, melody, control, note_text
            ),
            "padPolicy": pad_policy,
            "leadOctaveShift": 0,
            "harmonyPitches": {
                track_name: [int(note["midi"]) + offset for note in enabled]
                for track_name, offset in offsets.items()
            },
            "padBlocks": pad_blocks or [],
        }

    def write_plan(self, root: Path, plan: dict[str, Any]) -> Path:
        path = root / "bv_plan.json"
        path.write_text(
            json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def test_v1_is_default_and_v2_can_diagnose_v1_or_raw(self) -> None:
        fixture = ROOT / "test" / "fixtures" / "score_v3_valid"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for source in fixture.iterdir():
                if source.is_file():
                    (root / source.name).write_bytes(source.read_bytes())
            for with_plan in (True, False):
                with self.subTest(with_plan=with_plan):
                    if not with_plan:
                        (root / "bv_plan.json").unlink()
                    for args, expected_version in (([], "bv-score-v5"),
                                                   (["--evaluation-version", "v2"], "bv-diagnostics-v2")):
                        result = subprocess.run(
                            [sys.executable, str(SCRIPT_DIR / "validate_bv_music.py"), str(root), *args],
                            cwd=ROOT, capture_output=True, text=True,
                        )
                        self.assertEqual(result.returncode, 0, result.stderr)
                        report = json.loads(result.stdout)
                        self.assertEqual(report["scoreVersion"], expected_version)
                        self.assertTrue(report["passed"])
                        if expected_version == "bv-score-v5":
                            self.assertEqual(report["score"], 100)
                        else:
                            self.assertIsNone(report["score"])

    def test_validation_and_export_read_one_source_snapshot(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_source(root, 8)
            raw_path = bv_raw_to_midi.render_plan(self.write_plan(root, self.plan_payload(root)))
            for converter, operation in (
                (validate_bv_music.bv_raw_to_midi, lambda: validate_bv_music.validate(root)),
                (bv_raw_to_midi, lambda: bv_raw_to_midi.convert_bv_raw_to_midi(raw_path)),
            ):
                with self.subTest(operation=operation), patch.object(
                    converter, "read_json", wraps=converter.read_json,
                ) as read:
                    operation()
                    source_reads = [call for call in read.call_args_list
                                    if call.args[0].name in ("melody.json", "canonical.json")]
                    self.assertEqual(len(source_reads), 1)

    def test_committed_plan_fixture_expands_to_committed_raw_semantics(self) -> None:
        fixture = ROOT / "test" / "fixtures" / "score_v3_valid"
        with TemporaryDirectory() as temp_dir:
            rendered_path = Path(temp_dir) / "bv_raw.json"
            bv_raw_to_midi.render_plan(fixture / "bv_plan.json", rendered_path)

            self.assertEqual(
                json.loads(rendered_path.read_text(encoding="utf-8")),
                json.loads((fixture / "bv_raw.json").read_text(encoding="utf-8")),
            )

    def test_same_source_and_plan_render_byte_identically(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_source(root, 8)
            plan_path = self.write_plan(root, self.plan_payload(root))
            first = root / "first.json"
            second = root / "second.json"

            bv_raw_to_midi.render_plan(plan_path, first)
            bv_raw_to_midi.render_plan(plan_path, second)

            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_prepare_plan_render_validates_and_expands_without_writing(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_source(root, 4)
            plan_path = self.write_plan(root, self.plan_payload(root))

            resolved, plan, payload = bv_raw_to_midi.prepare_plan_render(plan_path)

            self.assertEqual(resolved, plan_path)
            self.assertEqual(plan["padPolicy"], "sectional")
            self.assertEqual(payload["header"], source_header(4))
            self.assertFalse((root / "bv_raw.json").exists())

    def test_default_output_is_atomic_and_requires_force_to_replace(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_source(root, 4)
            plan_path = self.write_plan(root, self.plan_payload(root))

            output = bv_raw_to_midi.render_plan(plan_path)
            original = output.read_bytes()
            with self.assertRaisesRegex(
                bv_raw_to_midi.BvMidiError, "already exists"
            ):
                bv_raw_to_midi.render_plan(plan_path)
            bv_raw_to_midi.render_plan(plan_path, force=True)

            self.assertEqual(output.read_bytes(), original)
            self.assertEqual(list(root.glob(".bv_raw.json.*.tmp")), [])

    def test_output_cannot_replace_plan_or_source_even_with_force(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_source(root, 4)
            plan_path = self.write_plan(root, self.plan_payload(root))
            original_plan = plan_path.read_bytes()

            with self.assertRaisesRegex(
                bv_raw_to_midi.BvMidiError, "must not replace a plan or source"
            ):
                bv_raw_to_midi.render_plan(
                    plan_path, output_path=plan_path, force=True
                )

            self.assertEqual(plan_path.read_bytes(), original_plan)

    def test_source_fingerprint_rejects_changed_source(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_source(root, 4)
            plan_path = self.write_plan(root, self.plan_payload(root))
            source = json.loads((root / "melody.json").read_text(encoding="utf-8"))
            source["melody"][0]["lyric"] = "changed"
            (root / "melody.json").write_text(
                json.dumps(source), encoding="utf-8"
            )

            with self.assertRaisesRegex(
                bv_raw_to_midi.BvMidiError, "sourceFingerprint does not match"
            ):
                bv_raw_to_midi.render_plan(plan_path)

    def test_fingerprint_covers_header_control_and_note_chart(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_source(
                root,
                4,
                control=[{"section": "verse", "time": 0, "duration": 4}],
            )
            header, melody, control, note_text = bv_raw_to_midi.load_source_bundle(root)
            baseline = bv_raw_to_midi.source_fingerprint(
                header, melody, control, note_text
            )
            changed_header = copy.deepcopy(header)
            changed_header["tempoMap"][0]["bpm"] = 121
            changed_control = copy.deepcopy(control)
            changed_control[0]["section"] = "chorus"

            self.assertNotEqual(
                baseline,
                bv_raw_to_midi.source_fingerprint(
                    changed_header, melody, control, note_text
                ),
            )
            self.assertNotEqual(
                baseline,
                bv_raw_to_midi.source_fingerprint(
                    header, melody, changed_control, note_text
                ),
            )
            self.assertNotEqual(
                baseline,
                bv_raw_to_midi.source_fingerprint(
                    header, melody, control, note_text + "\n"
                ),
            )

    def test_harmony_decision_count_must_match_enabled_melody(self) -> None:
        for adjustment in (-1, 1):
            with self.subTest(adjustment=adjustment), TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                self.write_source(root, 4)
                plan = self.plan_payload(root)
                if adjustment < 0:
                    plan["harmonyPitches"]["HHA"].pop()
                else:
                    plan["harmonyPitches"]["HHA"].append(64)
                plan_path = self.write_plan(root, plan)

                with self.assertRaisesRegex(
                    bv_raw_to_midi.BvMidiError,
                    "exactly 4 harmony-enabled melody decisions",
                ):
                    bv_raw_to_midi.render_plan(plan_path)

    def test_no_bv_filter_and_inherited_note_fields_are_exact(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            melody = self.write_source(
                root,
                4,
                control=[{"section": "no_bv", "time": 1, "duration": 1}],
            )
            plan = self.plan_payload(root)
            plan["leadOctaveShift"] = 12
            for pitches in plan["harmonyPitches"].values():
                for index in range(len(pitches)):
                    pitches[index] += 12
            plan_path = self.write_plan(root, plan)

            output = bv_raw_to_midi.render_plan(plan_path)
            raw = json.loads(output.read_text(encoding="utf-8"))

            self.assertEqual(len(raw["tracks"]["LeadDouble"]), 4)
            self.assertEqual(
                [note["midi"] for note in raw["tracks"]["LeadDouble"]],
                [72, 72, 72, 72],
            )
            self.assertEqual(len(raw["tracks"]["HHA"]), 3)
            self.assertEqual(
                [note["time"] for note in raw["tracks"]["HHA"]], [0, 2, 3]
            )
            for output_note, source_note in zip(
                raw["tracks"]["HHA"], (melody[0], melody[2], melody[3])
            ):
                self.assertEqual(output_note["time"], source_note["time"])
                self.assertEqual(output_note["duration"], source_note["duration"])
                self.assertEqual(output_note["lyric"], source_note["lyric"])
                self.assertEqual(
                    output_note["name"],
                    bv_raw_to_midi.midi_name(output_note["midi"]),
                )

    def test_v1_rejects_unimplemented_section_chunks(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_source(root, 4)
            plan = self.plan_payload(root)
            plan["sections"] = []

            with self.assertRaisesRegex(
                bv_raw_to_midi.BvMidiError, "unexpected key.*sections"
            ):
                bv_raw_to_midi.render_plan(self.write_plan(root, plan))

    def test_pad_blocks_expand_in_fixed_track_order(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_source(root, 4)
            plan = self.plan_payload(
                root,
                pad_blocks=[
                    {"time": 0, "duration": 2, "pitches": [72, 67, 64, 48]}
                ],
            )
            output = bv_raw_to_midi.render_plan(self.write_plan(root, plan))
            raw = json.loads(output.read_text(encoding="utf-8"))

            self.assertEqual(
                [raw["tracks"][track][0]["midi"] for track in bv_raw_to_midi.PAD_TRACKS],
                [72, 67, 64, 48],
            )
            self.assertTrue(
                all("lyric" not in raw["tracks"][track][0] for track in bv_raw_to_midi.PAD_TRACKS)
            )

    def test_pad_crossing_overlap_range_and_quantization_fail_closed(self) -> None:
        cases = {
            "crossing": [
                {"time": 0, "duration": 1, "pitches": [64, 72, 60, 48]}
            ],
            "overlap": [
                {"time": 0, "duration": 2, "pitches": [72, 67, 64, 48]},
                {"time": 1, "duration": 2, "pitches": [71, 67, 62, 50]},
            ],
            "range": [
                {"time": 0, "duration": 1, "pitches": [85, 67, 64, 48]}
            ],
            "unquantized": [
                {"time": 0.07, "duration": 1, "pitches": [72, 67, 64, 48]}
            ],
        }
        patterns = {
            "crossing": "without crossing",
            "overlap": "overlaps",
            "range": "MIDI 12..84",
            "unquantized": "unquantized",
        }
        for case, blocks in cases.items():
            with self.subTest(case=case), TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                self.write_source(root, 4)
                plan_path = self.write_plan(
                    root, self.plan_payload(root, pad_blocks=blocks)
                )

                with self.assertRaisesRegex(
                    bv_raw_to_midi.BvMidiError, patterns[case]
                ):
                    bv_raw_to_midi.render_plan(plan_path)

    def test_pad_sustain_into_no_bv_fails_closed(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_source(
                root,
                4,
                control=[{"section": "no_bv", "time": 2, "duration": 1}],
            )
            plan = self.plan_payload(
                root,
                pad_blocks=[
                    {"time": 1, "duration": 2, "pitches": [72, 67, 64, 48]}
                ],
            )

            with self.assertRaisesRegex(
                bv_raw_to_midi.BvMidiError, "overlaps no_bv"
            ):
                bv_raw_to_midi.render_plan(self.write_plan(root, plan))

    def test_renderer_preserves_non_chord_pitch_without_silent_fix(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_source(root, 4, chord="C")
            plan = self.plan_payload(root, pad_policy="optional")
            plan["harmonyPitches"]["HHA"][0] = 66
            output = bv_raw_to_midi.render_plan(self.write_plan(root, plan))
            raw = json.loads(output.read_text(encoding="utf-8"))

            self.assertEqual(raw["tracks"]["HHA"][0]["midi"], 66)
            self.assertEqual(raw["tracks"]["HHA"][0]["name"], "F#4")

    def test_rendered_fixture_passes_validator_and_midi_exporter(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_source(
                root,
                8,
                control=[
                    {"section": "verse", "time": 0, "duration": 4},
                    {"section": "chorus", "time": 4, "duration": 4},
                ],
            )
            plan = self.plan_payload(
                root,
                pad_blocks=[
                    {"time": 4, "duration": 2, "pitches": [70, 64, 57, 48]}
                ],
            )
            raw_path = bv_raw_to_midi.render_plan(self.write_plan(root, plan))

            report, exit_code = validate_bv_music.validate(raw_path, min_score=80)
            midi_path = bv_raw_to_midi.convert_bv_raw_to_midi(raw_path)

            self.assertEqual(exit_code, 0, report)
            self.assertTrue(report["passed"])
            self.assertEqual(report["musicalGateFailures"], [])
            self.assertTrue(midi_path.exists())
            self.assertEqual(midi_path.read_bytes()[:4], b"MThd")

    def test_validator_uses_plan_policy_by_default(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_source(
                root,
                8,
                control=[
                    {"section": "verse", "time": 0, "duration": 4},
                    {"section": "chorus", "time": 4, "duration": 4},
                ],
            )
            plan = self.plan_payload(
                root,
                pad_policy="required",
                pad_blocks=[
                    {"time": 4, "duration": 2, "pitches": [70, 64, 57, 48]}
                ],
            )
            raw_path = bv_raw_to_midi.render_plan(self.write_plan(root, plan))

            report, exit_code = validate_bv_music.validate(raw_path, min_score=80)

        self.assertEqual(exit_code, 1)
        self.assertFalse(report["passed"])
        self.assertEqual(report["padPolicy"], "required")
        self.assertEqual(report["policySource"], "plan")
        self.assertTrue(report["planDetected"])
        self.assertTrue(report["planFingerprintVerified"])
        self.assertTrue(report["rawMatchesPlan"])
        self.assertIn("padPresence", report["floorFailures"])
        blocker_kinds = [item["kind"] for item in report["deliveryBlockers"]]
        self.assertIn("musicalGate", blocker_kinds)
        self.assertIn("categoryFloor", blocker_kinds)
        self.assertLess(
            blocker_kinds.index("musicalGate"),
            blocker_kinds.index("categoryFloor"),
        )
        self.assertTrue(
            all(
                item["category"] == "padPresence"
                for item in report["musicalGateFailures"]
            )
        )

    def test_validator_rejects_conflicting_plan_policy_without_override(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_source(root, 4)
            plan = self.plan_payload(root, pad_policy="required")
            raw_path = bv_raw_to_midi.render_plan(self.write_plan(root, plan))

            report, exit_code = validate_bv_music.validate(
                raw_path, min_score=0, pad_policy="sectional"
            )

        self.assertEqual(exit_code, 1)
        self.assertFalse(report["passed"])
        self.assertEqual(report["padPolicy"], "required")
        self.assertEqual(report["requestedPadPolicy"], "sectional")
        self.assertEqual(report["policySource"], "plan")
        self.assertTrue(any("conflicts" in item for item in report["hardErrors"]))

    def test_diagnostic_policy_override_scores_but_cannot_pass_delivery(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_source(
                root,
                8,
                control=[
                    {"section": "verse", "time": 0, "duration": 4},
                    {"section": "chorus", "time": 4, "duration": 4},
                ],
            )
            plan = self.plan_payload(
                root,
                pad_policy="required",
                pad_blocks=[
                    {"time": 4, "duration": 2, "pitches": [70, 64, 57, 48]}
                ],
            )
            raw_path = bv_raw_to_midi.render_plan(self.write_plan(root, plan))

            report, exit_code = validate_bv_music.validate(
                raw_path,
                min_score=80,
                pad_policy="sectional",
                allow_policy_override=True,
            )

        self.assertEqual(exit_code, 1)
        self.assertFalse(report["passed"])
        self.assertFalse(report["deliveryEligible"])
        self.assertTrue(report["scoreThresholdMet"])
        self.assertTrue(report["policyOverridden"])
        self.assertEqual(report["policySource"], "cliOverride")
        self.assertTrue(any("diagnostic scoring" in item for item in report["warnings"]))
        self.assertTrue(
            any(
                item["code"] == "DIAGNOSTIC_POLICY_OVERRIDE"
                for item in report["deliveryBlockers"]
            )
        )

    def test_validator_rejects_raw_that_is_stale_against_plan(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_source(
                root,
                8,
                control=[
                    {"section": "verse", "time": 0, "duration": 4},
                    {"section": "chorus", "time": 4, "duration": 4},
                ],
            )
            plan = self.plan_payload(
                root,
                pad_blocks=[
                    {"time": 4, "duration": 2, "pitches": [70, 64, 57, 48]}
                ],
            )
            plan_path = self.write_plan(root, plan)
            raw_path = bv_raw_to_midi.render_plan(plan_path)
            plan["padBlocks"][0]["pitches"][0] = 71
            self.write_plan(root, plan)

            report, exit_code = validate_bv_music.validate(raw_path, min_score=0)

        self.assertEqual(exit_code, 1)
        self.assertFalse(report["passed"])
        self.assertTrue(report["planFingerprintVerified"])
        self.assertFalse(report["rawMatchesPlan"])
        self.assertTrue(any("does not match" in item for item in report["hardErrors"]))

    def test_validator_rejects_plan_after_source_fingerprint_changes(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_source(root, 4)
            plan_path = self.write_plan(root, self.plan_payload(root))
            raw_path = bv_raw_to_midi.render_plan(plan_path)
            (root / "note.txt").write_text(
                "Bar 1: Time Signature 4/4, BPM 120\n"
                "Bar 1: beat 1 F\nBar 2: beat 1 F\n",
                encoding="utf-8",
            )

            report, exit_code = validate_bv_music.validate(raw_path, min_score=0)

        self.assertEqual(exit_code, 1)
        self.assertFalse(report["passed"])
        self.assertTrue(report["planDetected"])
        self.assertFalse(report["planFingerprintVerified"])
        self.assertIsNone(report["rawMatchesPlan"])
        self.assertTrue(
            any("sourceFingerprint does not match" in item for item in report["hardErrors"])
        )

    def test_invalid_raw_report_still_identifies_verified_plan_policy(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_source(root, 4)
            plan = self.plan_payload(root, pad_policy="required")
            raw_path = bv_raw_to_midi.render_plan(self.write_plan(root, plan))
            raw_path.write_text("{}", encoding="utf-8")

            report, exit_code = validate_bv_music.validate(raw_path, min_score=0)

        self.assertEqual(exit_code, 1)
        self.assertFalse(report["passed"])
        self.assertEqual(report["padPolicy"], "required")
        self.assertEqual(report["policySource"], "plan")
        self.assertTrue(report["planFingerprintVerified"])
        self.assertIsNone(report["rawMatchesPlan"])
        self.assertNotEqual(report["hardErrors"], [])

    def test_plan_is_at_least_seventy_percent_smaller_for_two_lengths(self) -> None:
        for note_count in (32, 128):
            with self.subTest(note_count=note_count), TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                self.write_source(root, note_count)
                plan_path = self.write_plan(
                    root,
                    self.plan_payload(root, pad_policy="optional"),
                )
                raw_path = bv_raw_to_midi.render_plan(plan_path)

                ratio = plan_path.stat().st_size / raw_path.stat().st_size

                self.assertLessEqual(ratio, 0.30)


class BvPlanV2Test(unittest.TestCase):
    write_source = BvPlanToRawTest.write_source
    legacy_plan = BvPlanToRawTest.plan_payload

    def setup_plan(self, root, control=None):
        self.write_source(root, 8, control=control)
        plan = self.legacy_plan(root)
        plan['schemaVersion'] = 'bv-plan-v2'
        plan['padPolicy'] = 'free'
        plan['harmonyPitches'] = {name: [None]*8 for name in bv_raw_to_midi.MELODY_ALIGNED_TRACKS}
        return plan

    def render(self, root, plan):
        (root/'bv_plan.json').write_text(json.dumps(plan))
        path = bv_raw_to_midi.render_plan(root, force=True)
        return json.loads(path.read_text())

    def test_cli_fingerprint_render_validate_and_export(self):
        def run(script, *args):
            return subprocess.run(
                [sys.executable, str(SCRIPT_DIR / script), *map(str, args)],
                cwd=ROOT, capture_output=True, text=True,
            )

        with TemporaryDirectory() as directory:
            root = Path(directory)
            plan = self.setup_plan(root)
            plan['harmonyPitches']['HHA'][3] = 64
            fingerprint = run('bv_raw_to_midi.py', root, '--print-source-fingerprint')
            self.assertEqual(fingerprint.returncode, 0, fingerprint.stderr)
            self.assertEqual(fingerprint.stdout.strip(), plan['sourceFingerprint'])
            self.assertFalse((root / 'bv_plan.json').exists())
            self.assertFalse((root / 'bv_raw.json').exists())

            (root / 'bv_plan.json').write_text(json.dumps(plan))
            raw_path = root / 'rendered.json'
            rendered = run('bv_raw_to_midi.py', root, '--render-plan', '-o', raw_path)
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            self.assertFalse((root / 'bv.mid').exists())
            original = raw_path.read_bytes()

            repeated = run('bv_raw_to_midi.py', root, '--render-plan', '-o', raw_path)
            self.assertEqual(repeated.returncode, 1)
            self.assertIn('already exists', repeated.stderr)
            self.assertEqual(raw_path.read_bytes(), original)
            replaced = run('bv_raw_to_midi.py', root / 'bv_plan.json', '--render-plan',
                           '-o', raw_path, '--force')
            self.assertEqual(replaced.returncode, 0, replaced.stderr)
            self.assertEqual(raw_path.read_bytes(), original)

            default_checked = run('validate_bv_music.py', raw_path)
            self.assertEqual(default_checked.returncode, 1)
            self.assertIn('--evaluation-version v2', json.loads(default_checked.stdout)['hardErrors'][0])
            checked = run('validate_bv_music.py', raw_path, '--evaluation-version', 'v2')
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertTrue(json.loads(checked.stdout)['passed'])
            midi_path = root / 'rendered.mid'
            exported = run('bv_raw_to_midi.py', raw_path, '-o', midi_path)
            self.assertEqual(exported.returncode, 0, exported.stderr)
            self.assertTrue(midi_path.read_bytes().startswith(b'MThd'))
            self.assertIn(b'L3', midi_path.read_bytes())

    def test_cli_rejects_conflicting_plan_options(self):
        for options in (
            ['--force'],
            ['--render-plan', '--print-source-fingerprint'],
            ['--print-source-fingerprint', '--force'],
            ['--print-source-fingerprint', '-o', 'raw.json'],
        ):
            with self.subTest(options=options):
                result = subprocess.run(
                    [sys.executable, str(SCRIPT_DIR / 'bv_raw_to_midi.py'),
                     'work', *options],
                    cwd=ROOT, capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn('error:', result.stderr)

    def test_sparse_indices_lyrics_export_and_diagnostics(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            plan = self.setup_plan(root)
            plan['harmonyPitches']['HHA'][3] = 64
            plan['harmonyPitches']['HHA'][6] = 74
            plan['harmonyPitches']['LHA'][5] = 55
            raw = self.render(root, plan)
            self.assertEqual([n['lyric'] for n in raw['tracks']['HHA']], ['L3','L6'])
            self.assertEqual([n['time'] for n in raw['tracks']['LHA']], [5])
            before = (root/'bv_raw.json').read_bytes()
            self.render(root, plan)
            self.assertEqual(before, (root/'bv_raw.json').read_bytes())
            report, code = validate_bv_music.validate(root, min_score=100, evaluation_version="v2")
            self.assertEqual(code, 0, report)
            self.assertIsNone(report['score'])
            self.assertEqual(report['deliveryBlockers'], [])
            self.assertEqual(report['musicReviewStatus'], 'pending')
            info = report['metrics']['tracks']['HHA']
            self.assertEqual(info['outsideOctave'][0]['time'], 6)
            self.assertIsNone(info['repeatedPitchRate'])
            self.assertEqual(info['largeLeaps'], []) # no transitions across rests
            midi = bv_raw_to_midi.convert_bv_raw_to_midi(root/'bv_raw.json')
            data = midi.read_bytes()
            self.assertTrue(data.startswith(b'MThd'))
            self.assertIn(b'L6', data)

    def test_no_bv_cuts_sustain_without_resuming_and_keeps_lead(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            plan = self.setup_plan(root, [{'time': 1.5, 'duration': 1.5, 'section': 'no_bv'}])
            plan['harmonyPitches']['HHA'][1] = 64
            plan['harmonyPitches']['HHA'][3] = 65
            plan['padBlocks'] = [{'time': 0, 'duration': 4, 'pitches': [72,67,60,48]}]
            raw = self.render(root, plan)
            self.assertEqual(raw['tracks']['HHA'][0]['duration'], .5)
            self.assertEqual(raw['tracks']['HHA'][1]['time'], 3)
            self.assertEqual(raw['tracks']['LeadDouble'][1]['duration'], 1)
            self.assertEqual(raw['tracks']['VocalPad1'][0]['duration'], 1.5)
            self.assertEqual(len(raw['tracks']['VocalPad1']), 1)
            report, code = validate_bv_music.validate(root, 80, evaluation_version="v2")
            self.assertEqual(code, 0, report)
            bv_raw_to_midi.convert_bv_raw_to_midi(root/'bv_raw.json')

    def test_rejects_onsets_inside_silence_and_wrong_direction(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            plan = self.setup_plan(root, [{'time': 2, 'duration': 1, 'section': 'no_bv'}])
            for mutation in ('harmony', 'pad', 'direction', 'count', 'boolean', 'policy'):
                with self.subTest(mutation=mutation):
                    bad = copy.deepcopy(plan)
                    if mutation == 'harmony': bad['harmonyPitches']['HHA'][2] = 64
                    if mutation == 'pad': bad['padBlocks'] = [{'time': 2, 'duration': 1, 'pitches': [72,67,60,48]}]
                    if mutation == 'direction': bad['harmonyPitches']['HHA'][0] = 55
                    if mutation == 'count': bad['harmonyPitches']['HHA'].pop()
                    if mutation == 'boolean': bad['harmonyPitches']['HHA'][0] = True
                    if mutation == 'policy': bad['padPolicy'] = 'optional'
                    with self.assertRaises(bv_raw_to_midi.BvMidiError):
                        self.render(root, bad)

    def test_empty_arrangement_is_unreviewed_not_high_scoring(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            plan = self.setup_plan(root)
            self.render(root, plan)
            report, code = validate_bv_music.validate(root, 80, evaluation_version="v2")
            self.assertEqual(code, 0, report)
            self.assertIsNone(report['score'])
            self.assertEqual(report['workflowStatus'], 'reviewRecommended')
            for info in report['metrics']['tracks'].values():
                self.assertEqual(info['contourEvidence'], 'insufficient')
                self.assertIsNone(info['nonChordRate'])
            report, code = validate_bv_music.validate(root, 80, pad_policy='optional', allow_policy_override=True, evaluation_version="v2")
            self.assertEqual(code, 1)

    def test_tampered_raw_and_stale_source_fail_validation_and_export(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            plan = self.setup_plan(root)
            plan['harmonyPitches']['HHA'][3] = 64
            raw = self.render(root, plan)
            raw['tracks']['HHA'][0]['midi'] = 65
            raw['tracks']['HHA'][0]['name'] = 'F4'
            (root/'bv_raw.json').write_text(json.dumps(raw))
            report, code = validate_bv_music.validate(root, 80, evaluation_version="v2")
            self.assertEqual(code, 1)
            self.assertFalse(report['rawMatchesPlan'])
            with self.assertRaises(bv_raw_to_midi.BvMidiError):
                bv_raw_to_midi.convert_bv_raw_to_midi(root/'bv_raw.json')
            self.render(root, plan)
            (root/'note.txt').write_text((root/'note.txt').read_text()+'\n')
            report, code = validate_bv_music.validate(root, 80, evaluation_version="v2")
            self.assertEqual(code, 1)
            self.assertFalse(report['planFingerprintVerified'])

    def test_cross_chord_evidence_is_localized_and_nonblocking(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            plan = self.setup_plan(root)
            (root/'note.txt').write_text('Time Unit: beats\n\nBar 1: Time Signature 4/4, BPM 120\nBar 1: beat 1 C | beat 3 F#\nBar 2: beat 1 C\n')
            plan['sourceFingerprint'] = bv_raw_to_midi.fingerprint_for_path(root)
            plan['padBlocks'] = [{'time': 0, 'duration': 4, 'pitches': [72,67,64,48]}]
            self.render(root, plan)
            report, code = validate_bv_music.validate(root, 80, evaluation_version="v2")
            self.assertEqual(code, 0, report)
            issues = report['metrics']['tracks']['VocalPad1']['nonChordSpans']
            self.assertEqual(issues, [{'time': 2.0, 'end': 4.0, 'midi': 72, 'chord': 'F#'}])

    def test_original_index_order_does_not_require_chronological_arrays(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            plan = self.setup_plan(root)
            source = json.loads((root/'melody.json').read_text())
            source['melody'].reverse()
            (root/'melody.json').write_text(json.dumps(source))
            plan['sourceFingerprint'] = bv_raw_to_midi.fingerprint_for_path(root)
            plan['harmonyPitches']['HHA'][0] = 64 # time 7 in original source order
            plan['harmonyPitches']['HHA'][4] = 67 # time 3
            raw = self.render(root, plan)
            self.assertEqual([n['time'] for n in raw['tracks']['HHA']], [7,3])
            self.assertEqual([n['lyric'] for n in raw['tracks']['HHA']], ['L7','L3'])
            report, code = validate_bv_music.validate(root, 80, evaluation_version="v2")
            self.assertEqual(code, 0, report)
            self.assertEqual(report['metrics']['tracks']['HHA']['activitySpans'], [[3,4],[7,8]])
            bv_raw_to_midi.convert_bv_raw_to_midi(root/'bv_raw.json')



if __name__ == "__main__":
    unittest.main()
