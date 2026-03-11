---
name: background-vocals
description: Use in this repository after =hs routing selects a concrete background-vocal task. Arrange melody.json and note.txt into an eleven-track bv_raw.json, then validate and revise it.
---

# Background Vocals

## Purpose

Read:

```text
output/<work_name>/melody.json
output/<work_name>/note.txt
```

Write:

```text
output/<work_name>/bv_raw.json
```

Use this skill only after `rule/modes.md` selects an executable `=hs` task.

## Hard Boundary

This is an AI arrangement task, not deterministic chord-tone conversion.

- Read `melody.json` and `note.txt` directly and make the musical decisions.
- Never create scripts, generated helper code, intermediate summaries, context
  JSON, arrangement-plan files, or other helper inputs. This prohibition cannot
  be overridden by a user request during an `=hs` task.
- Do not create, edit, delete, rename, or temporarily modify code, tests,
  skills, rules, configuration, or documentation.
- Existing scripts and read-only inline commands may be used only for
  inspection, validation, scoring, and report-driven revision.
- The only persistent file this task may create or modify is the requested
  `output/<work_name>/bv_raw.json`.

If existing tools prevent completion or validation, stop and report the
limitation instead of modifying them.

## Input Contract

`melody.json` provides the source melody and header. Preserve the full header,
including `startTime`, `duration`, `timeUnit`, `grid`, `tempoMap`, and
`timeSignatureMap`.

All `time` and `duration` values are in beats. Stop if `header.timeUnit` is not
`beats`.

`note.txt` is the required bar-by-bar chord chart. If it is missing or stale,
stop and ask the user to run `=hx` for the same work first.

Optional `control` spans from the MIDI `control` track may use:

- `verse`, `pre_chorus`, `chorus`, `bridge`
- `no_bv`

## Output Contract

Write valid JSON with exactly the top-level keys `header` and `tracks`.

Always create these track arrays, even when pad tracks are empty:

```text
LeadDouble
HHA HHB HHF
LHA LHB LHF
VocalPad1 VocalPad2 VocalPad3 VocalPad4
```

Every note contains `name`, `midi`, `time`, and `duration`.

For `LeadDouble` and the six melody-aligned harmony tracks, copy the
corresponding source melody note's optional `lyric` exactly; omit it when the
source has none. Pad notes never contain `lyric`. Emit no other note fields.

## Arrangement Contract

### LeadDouble and register

- Preserve every source melody note's `time` and `duration`.
- Normally copy the source pitch exactly.
- Current `midi_to_json.py` output should already be normalized into a useful
  harmony register. For legacy or manually edited input only, use the smallest
  feasible uniform whole-octave shift when necessary.
- Apply any shift to every `LeadDouble` note, updating both `midi` and `name`.
  Never shift individual notes independently or use a non-octave correction.
- Use adjusted `LeadDouble` as the reference for all harmony direction,
  interval, contour, and spacing decisions.
- If no single octave shift leaves useful room above and below, stop and report
  the register conflict.

### Melody-aligned harmony

Outside explicit `no_bv` spans, each of `HHA`, `HHB`, `HHF`, `LHA`, `LHB`, and
`LHF` must contain exactly one note per source melody note, with matching
`time`, `duration`, and optional `lyric`.

- These are true harmony lines, not free counter-lines, sparse phrase responses,
  long pad notes, or bass support.
- Upper tracks stay above and use a different pitch from adjusted `LeadDouble`.
- Lower tracks stay below and use a different pitch from adjusted `LeadDouble`.
- `HHA`, `HHB`, `LHA`, and `LHB` are expressive lines that substantially follow
  the melody contour, generally 2-4 scale degrees away while fitting the chord.
  Occasional 5th-degree spacing is allowed when it improves the line. Do not let
  paired expressive lines duplicate for long stretches or collapse into one
  repeated pitch for most notes in a measure.
- `HHF` and `LHF` are flatter melody-aligned lines that may favor repeated chord
  tones and common tones, but they are not pads.

### Pads and sections

- Arrange `VocalPad1-4` as smooth soprano/alto/tenor/bass-like choir support
  with controlled spacing, natural inversions, common tones, and contrary or
  oblique motion. They may rest when pads would clutter the arrangement; avoid
  rigid parallel blocks.
- `verse` favors intimate, narrow, common-tone voicing and restrained pads.
- `pre_chorus` builds lift, directional motion, and controlled width.
- `chorus` supports the hook with fuller voicing, stronger upper harmonies, and
  useful pad density.
- `bridge` may contrast register, voicing color, and contour while remaining
  singable.
- In `no_bv` spans, leave all harmony and pad tracks silent; `LeadDouble`
  remains complete.

### Global music rules

- Every harmony or pad note must fit the active chord from `note.txt`.
- In `N.C.` spans outside `no_bv`, harmony tracks remain complete using nearby
  tonal context or conservative consonance; pads may rest.
- Keep every note within MIDI `12..84` (`C0..C6`).
- Use beats and align notes to the active `1/32_bar` grid or triplets.
- Do not cross bar boundaries unless the duration intentionally sustains across
  the boundary.

## Workflow

1. Read and validate `melody.json`, `note.txt`, header, controls, and register.
2. Copy the header and create all eleven track arrays.
3. Write `LeadDouble`, applying a uniform octave fallback only when required.
4. Arrange the six complete melody-aligned harmony tracks.
5. Arrange the four pad tracks and apply section guidance.
6. Strip disallowed fields and write `output/<work_name>/bv_raw.json`.
7. Run:

   ```bash
   python3 script/validate_bv_music.py output/<work_name>
   ```

8. Fix hard errors. Revise clear musical problems or a score below the default
   threshold, rerunning validation for up to three revision rounds.
9. Confirm no non-output repository file changed during the task.

Do not replace the arrangement with script-generated content.

## Completion Gate

Do not deliver `bv_raw.json` unless:

- JSON shape, required tracks, allowed fields, inherited lyrics, header, range,
  beat/grid alignment, and chord fit satisfy the contracts above.
- `LeadDouble` timing is exact and its pitch is either exact or one uniform
  whole-octave shift.
- Outside `no_bv`, all six harmony tracks align one-to-one with source melody
  timing and stay on the required side of adjusted `LeadDouble`.
- Expressive harmony lines retain meaningful contour.
- `script/validate_bv_music.py` passes hard validation.
- No repository file other than the requested `bv_raw.json` changed.

Mention any remaining intentional soft warnings in the final response.
