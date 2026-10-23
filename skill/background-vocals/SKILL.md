---
name: background-vocals
description: Arrange a concrete =hs work into bv_plan.json and bv_raw.json with deterministic rendering and v1 scoring; select v2 diagnostics explicitly.
---

# Background Vocals

Use after `rule/modes.md` routes an executable `=hs` request. Read the work's
`melody.json` and `note.txt`; author `bv_plan.json`, then render `bv_raw.json`.
For missing or demonstrably stale charts, follow the same-work chord-chart
prerequisite policy in `rule/modes.md`; do not request a separate mode selection.

## Creative contract

Write an arrangement that supports the lead with natural lines, useful space,
and development across phrases and sections. Choose voice combinations and Pad
activity from the song and the user's intent. Default to v1 plans and scoring;
use v2 when the request explicitly selects `--evaluation-version v2`.

- Keep LeadDouble complete. Default to the source pitches; only use a necessary,
  uniform whole-octave shift. All harmony directions reference this adjusted lead.
- HHA/HHB and LHA/LHB are expressive upper/lower harmony lines; HHF/LHF favor
  stable chord/common tones. These are roles, not per-note formulas.
- In v1, each of the six lines covers every source melody onset outside `no_bv`,
  with the source duration. V2 permits rests; shape its entrances and exits around
  phrases, with silence-boundary truncation as described below.
- Upper harmonies stay above the lead, lower harmonies below. Prefer 1–12
  semitones of separation; brief wider intervals need a musical reason. Assess
  the combined voicing for clarity rather than crowding every part near the lead.
- VocalPad1–4 provide independent four-part support, ordered high to low. They
  need not follow the lead's octave window and may rest freely.
- Consider each sustained note across all chords it overlaps. Repetition,
  parallel motion, dissonance and wider spacing require contextual judgment,
  not automatic removal. `N.C.` permits contextual harmony or rests.
- Keep LeadDouble complete through `no_bv`. In v1, omit harmony decisions whose
  source onset is inside `no_bv` and keep Pad notes outside it. In v2, use null
  for those onsets; renderer also cuts background sustains at the boundary.
- Keep all sounding pitches within MIDI 12–84 and timing on the source beat/grid
  contract. Honor explicit user requests about texture and density.

## Plan format

The plan's `schemaVersion` controls indexing, rests, Pad policy and `no_bv`
rendering. `--evaluation-version` selects validation only: v2 diagnostics on an
existing v1 plan retain that plan's v1 rules.

Use exactly these six top-level keys. This example shows the shape for one
source melody note; obtain the fingerprint with the command below and write
pitches and Pad timing for the actual song.

```json
{
  "schemaVersion": "bv-plan-v1",
  "sourceFingerprint": "<current source fingerprint>",
  "padPolicy": "sectional",
  "leadOctaveShift": 0,
  "harmonyPitches": {
    "HHA": [64], "HHB": [67], "HHF": [72],
    "LHA": [55], "LHB": [52], "LHF": [48]
  },
  "padBlocks": [{"time": 0, "duration": 4, "pitches": [72, 67, 60, 48]}]
}
```

- V1 arrays index source melody notes in their original order, excluding onsets
  inside `no_bv`. Every remaining note needs an integer pitch in each array.
  Default `padPolicy` is `sectional`; use `optional` or `required` when the user's
  Pad intent calls for it, and satisfy that policy's scoring gates.
- For explicit v2 work, set `schemaVersion: "bv-plan-v2"` and `padPolicy: "free"`.
  Arrays index the complete source melody, including `no_bv` onsets. Integers
  sound, `null` rests, and all onsets inside `no_bv` must be null. Choose density
  from the music. Preserve an existing plan's schema and indexing rules.
- `leadOctaveShift` is a whole-octave integer between -72 and 72, applied uniformly
  to LeadDouble. Sounding pitches use JSON integers within MIDI 12–84.
- Each Pad block has exactly `time`, `duration`, and four `pitches` ordered from
  VocalPad1 to VocalPad4, strictly descending. Blocks are sorted, non-overlapping,
  within the piece and on the source grid. Use an empty array for omitted Pad.
- Renderer copies source timing and lyrics for aligned voices and calculates note
  names. V2 cuts background sustains at the first `no_bv` boundary; later notes
  follow their explicit plan entries. LeadDouble keeps its complete source timing.
- The fingerprint covers header, melody, control and the exact chart text. Refresh
  it after source changes, revise the plan and rerender. Validator and exporter
  verify the plan against the same source and compare its expansion with raw.

## Author and render

```bash
python3 script/bv_raw_to_midi.py output/<work_name> --print-source-fingerprint
python3 script/bv_raw_to_midi.py output/<work_name>/bv_plan.json --render-plan
python3 script/validate_bv_music.py output/<work_name>
```

Append `--evaluation-version v2` to validation for v2 work. A v2 plan requires
that flag; v1 plans and complete old raw can also use v2 diagnostics.

Obtain the fingerprint, write every musical decision in the plan, then render
and validate. Use `--force` when deliberately replacing raw after a plan revision.
Never hand-edit raw, generate pitches with helper code, or optimize a score by
automatic search. Read-only inspection and existing deterministic tools are allowed.
Only the requested plan/raw and any prerequisite `note.txt` permitted by
`rule/modes.md` may persistently change. The prerequisite uses the chord-chart
workflow and its narrowly scoped temporary compatibility procedure; during
arrangement and raw validation, do not modify tools or source files to get a pass.
Report tool limitations when blocked.

## Review and finish

Fix hard errors in the plan and rerender. In v1, also address delivery blockers
and meet the default minimum score of 80. Revise until the report passes or no
supported musical improvement remains. Report the score and remaining blockers.

V2 uses technical validation and music review. `passed=true` confirms technical
checks, source fingerprint and raw/plan agreement. Diagnostics localize evidence.

For v2, after technical validation request an independent agent review using
[the review brief](references/music-review.md). Supply the user's musical intent,
source and final plan/raw; let the reviewer judge these before reading diagnostics.
If delegation is unavailable, disclose that independent review was not performed.
Review provides musical recommendations. Resolve hard errors before delivery.

Revise only for supported musical reasons, rerender and revalidate after changes,
and request another review only for materially changed passages. Stop when there
is no evidence-backed improvement to make; surface unresolved musical differences.
Deliver links to plan/raw, the technical result, review status, and significant
intentional exceptions. Do not claim listening review unless audio was heard.
