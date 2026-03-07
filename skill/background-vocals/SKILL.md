---
name: background-vocals
description: Use in this repository when the user is in =hs background-vocals mode and asks to process a specific work directory. Converts output/<work_name>/melody.json plus output/<work_name>/note.txt into output/<work_name>/bv_raw.json containing eleven background-vocal tracks that follow the melody timing, chord chart, tempo map, and time-signature map.
---

# Background Vocals

## Purpose

Generate background-vocal arrangement JSON from:

```text
output/<work_name>/melody.json
output/<work_name>/note.txt
```

and write:

```text
output/<work_name>/bv_raw.json
```

Use this skill only after project mode routing has selected `=hs`.

## Role Boundary

This skill is an AI arrangement task, not a deterministic conversion task.

`note.txt` and `melody.json` are already the required compressed inputs. Do not create another intermediate summary, context JSON, arrangement-plan file, or generated helper input unless the user explicitly asks for it.

The agent should read `note.txt` and `melody.json` directly, make musical arrangement decisions, and write `bv_raw.json`.

Scripts or shell commands may be used only for inspection, validation, scoring, and report-driven revision. Do not use scripts to generate the harmony or pad content.

## Input Contract

`melody.json` provides the source melody and header. The header contains:

- `startTime`
- `duration`
- `timeUnit`
- `grid`
- `tempoMap`
- `timeSignatureMap`

`note.txt` is the bar-by-bar chord chart produced by the `chord-chart` skill.

`melody.json` may also contain optional `control` section spans from the MIDI `control` track:

- `verse`
- `pre_chorus`
- `chorus`
- `bridge`
- `no_bv`

All `time` and `duration` values are in beats. If `note.txt` is missing or stale, stop and ask the user to run `=hx` for the same work first.

## Output Contract

Write valid JSON only to `bv_raw.json`.

Top-level shape:

```json
{
  "header": {
    "startTime": 0,
    "duration": 64,
    "timeUnit": "beats",
    "grid": "1/32_bar",
    "tempoMap": [],
    "timeSignatureMap": []
  },
  "tracks": {
    "LeadDouble": [],
    "HHA": [],
    "HHB": [],
    "HHF": [],
    "LHA": [],
    "LHB": [],
    "LHF": [],
    "VocalPad1": [],
    "VocalPad2": [],
    "VocalPad3": [],
    "VocalPad4": []
  }
}
```

Each note object must contain exactly:

```json
{
  "name": "C4",
  "midi": 60,
  "time": 0,
  "duration": 1
}
```

Do not emit velocity, CC, pitch bend, lyrics, phonemes, singer parameters, comments, or extra metadata.

## Required Tracks

Always create these eleven tracks, even if pad tracks are empty:

- `LeadDouble`
- `HHA`
- `HHB`
- `HHF`
- `LHA`
- `LHB`
- `LHF`
- `VocalPad1`
- `VocalPad2`
- `VocalPad3`
- `VocalPad4`

## Arrangement Rules

- `LeadDouble` must preserve every source melody note's `time` and `duration`. By default it also copies the source pitch exactly.
- `melody.json` produced by the current `midi_to_json.py` should already be normalized by one uniform whole-octave shift into the harmony working register. Before arranging harmony, verify that it still has useful upper/lower harmony headroom.
- Only as a fallback for legacy or manually edited `melody.json`, if the melody still sits too high or too low, transpose the entire working melody and `LeadDouble` by the minimum necessary whole number of octaves.
- Any register correction must be one uniform whole-octave shift applied to every `LeadDouble` note. Update both `midi` and `name`. Never transpose individual melody notes independently, and never use a non-octave shift for register correction.
- After register correction, treat the adjusted `LeadDouble` as the main-vocal reference for all upper/lower harmony direction, interval, contour, and spacing decisions.
- Prefer no shift. When a shift is necessary, choose the smallest absolute octave shift that leaves useful room for both upper and lower harmonies while keeping `LeadDouble` in range. If no single octave shift can make the arrangement feasible, stop and report the register conflict.
- `HHA`, `HHB`, `HHF`, `LHA`, `LHB`, and `LHF` are true harmony lines, not free counter-lines or pads. Each of these six tracks must contain exactly one note for every source melody note, with the same `time` and `duration` as that melody note.
- Exception: in explicit `control` spans whose `section` is `no_bv`, do not generate any notes for `HHA`, `HHB`, `HHF`, `LHA`, `LHB`, `LHF`, or pad tracks. `LeadDouble` must still preserve every melody note's timing and use the same selected uniform octave shift as the rest of the song.
- Use `verse`, `pre_chorus`, `chorus`, and `bridge` control spans to shape harmony style, density, pad entrances, register, voicing width, rhythmic energy, and intensity. They do not relax the requirement that harmony lines must be complete and melody-aligned outside `no_bv` spans.
- Section style guidance:
  - `verse`: more intimate voicing, narrower range, smoother common-tone writing, restrained pads.
  - `pre_chorus`: more lift and forward motion, slightly wider voicing, clearer directional contour, controlled pad build.
  - `chorus`: fuller hook support, broader voicing, stronger upper harmonies, denser pads when they help the hook.
  - `bridge`: allow contrast, alternate registers or voicing color, and less predictable contour while staying singable.
- `HHA` is an expressive upper harmony that follows the original melody contour. It must stay above the main melody, use a different pitch from the melody, and generally sit 2-4 scale degrees above the melody while fitting the active chord. Occasional 5th-degree spacing is allowed when it improves the line.
- `HHB` is a second expressive upper harmony that follows the original melody contour. It must stay above the main melody, use a different pitch from the melody, and generally sit 2-4 scale degrees above the melody while fitting the active chord. It should not duplicate `HHA` for long stretches.
- `HHF` is the flatter upper harmony. It must stay above the main melody and use the same `time` and `duration` as the melody, but it may favor repeated chord tones, common tones, and smaller contour changes. It should still be written as separate melody-aligned notes, not long pad notes.
- `LHA` is an expressive lower harmony that follows the original melody contour. It must stay below the main melody, use a different pitch from the melody, and generally sit 2-4 scale degrees below the melody while fitting the active chord. Do not place it too low or turn it into bass support.
- `LHB` is a second expressive lower harmony that follows the original melody contour. It must stay below the main melody, use a different pitch from the melody, and generally sit 2-4 scale degrees below the melody while fitting the active chord. It should not duplicate `LHA` for long stretches.
- `LHF` is the flatter lower harmony. It must stay below the main melody and use the same `time` and `duration` as the melody, but it may favor repeated chord tones, common tones, and smaller contour changes. It should still be written as separate melody-aligned notes, not long pad notes.
- `VocalPad1`, `VocalPad2`, `VocalPad3`, and `VocalPad4` are pad/choir support voices. Write them in a four-part harmony style: soprano/alto/tenor/bass-like spacing, smooth voice leading, common tones, contrary or oblique motion, controlled spacing, and natural chord inversions. They do not need to move in parallel or in the same direction, and they may rest when pads would clutter the arrangement.
- Every arranged harmony or pad note must fit the active chord from `note.txt`.
- For `N.C.` spans, `HHA`, `HHB`, `HHF`, `LHA`, `LHB`, and `LHF` are still required for every melody note outside explicit `no_bv` spans. Use the nearest musically implied chord context, surrounding tonal center, or a conservative consonant interval choice. Pads may rest in `N.C.` spans.
- All arranged notes must stay within `C0` through `C6`, inclusive.
- Notes must use beats for `time` and `duration`.
- Notes must align to the active `1/32_bar` grid or triplet subdivisions.
- Notes must not cross time-signature bar boundaries unless intentionally sustained across a boundary by duration.
- Preserve the full `header`, especially `tempoMap` and `timeSignatureMap`.

## Do Not

- Do not create a new script whose purpose is to generate `bv_raw.json`.
- Do not create an extra compressed input file before arranging.
- Do not treat harmony writing as a deterministic chord-tone mapping problem.
- Do not make `HHA`, `HHB`, `HHF`, `LHA`, `LHB`, or `LHF` into sparse lines, long pad notes, or partial phrase responses.
- Do not make `HHA`, `HHB`, `LHA`, and `LHB` so conservative that most notes in a measure collapse to one repeated pitch. They should substantially follow the source melody contour.
- Do not make `VocalPad1`, `VocalPad2`, `VocalPad3`, and `VocalPad4` move as rigid parallel block chords throughout the song.
- Do not leave temporary helper files behind.

## Allowed Mechanical Checks

The agent may use temporary commands or scripts only to validate and score the finished `bv_raw.json`.

Validation may check:

- JSON syntax and top-level shape.
- Required track names.
- Exact `LeadDouble` timing copy from `melody.json`, with either identical pitch or one uniform whole-octave pitch shift across the entire track.
- Exact `time` and `duration` alignment between every harmony-enabled melody note and every note in `HHA`, `HHB`, `HHF`, `LHA`, `LHB`, and `LHF`.
- Allowed note fields only.
- MIDI range `C0..C6`.
- Beat/grid alignment.
- Whether arranged notes fit the active chord from `note.txt`.
- Soft music risks reported by `script/validate_bv_music.py`, including contour collapse, excessive leaps, duplicate harmony lines, wide spacing, and likely accidental non-chord tones.

Temporary validation files must be removed before finishing.

## Workflow

1. Read `output/<work_name>/melody.json`.
2. Read `output/<work_name>/note.txt`.
3. Validate `header.timeUnit` is `beats`; if it is not, stop and report the mismatch.
4. Use the chord chart directly as the harmonic reference. Parse active chord spans only as needed for validation or local reasoning; do not create another intermediate input artifact.
5. Copy the melody header into `bv_raw.json.header`.
6. Create all eleven required track arrays.
7. Verify the melody register normalized by `midi_to_json.py`. Only for legacy or manually edited input that still lacks headroom, choose the smallest feasible uniform whole-octave fallback shift.
8. Copy melody timing and pitch exactly into `LeadDouble`; only when the fallback shift is required, update every LeadDouble note's `midi` and `name`.
9. Arrange `HHA`, `HHB`, `HHF`, `LHA`, `LHB`, and `LHF` as complete melody-aligned harmony lines relative to the adjusted `LeadDouble`: one note per harmony-enabled melody note, same `time`, same `duration`, different pitch, and above/below placement as defined above.
10. If optional `control` data exists, apply `no_bv` spans as hard silence for harmony and pad tracks, while using other section labels as harmony-style and arrangement-intensity guidance.
11. Arrange `VocalPad1`, `VocalPad2`, `VocalPad3`, and `VocalPad4` as smooth four-part-style support voices, not rigid parallel blocks.
12. Strip all note fields except `name`, `midi`, `time`, and `duration`.
13. Write `output/<work_name>/bv_raw.json`.
14. Run:

```bash
python3 script/validate_bv_music.py output/<work_name>
```

15. If the report has hard errors, revise `bv_raw.json` until hard validation passes.
16. If the report score is below the default passing threshold or the warnings identify clear musical problems, revise the arrangement directly and rerun the validator. Iterate up to three validation/revision rounds. Do not replace the arrangement with script-generated content.

## Validation Checklist

Before finishing:

- `bv_raw.json` is valid JSON.
- The top-level keys are exactly `header` and `tracks`.
- All eleven required tracks exist.
- `LeadDouble` exactly matches source melody `time` and `duration`.
- `LeadDouble` pitch either matches source melody exactly or uses one uniform whole-octave shift for every note; its `name` matches its shifted `midi`.
- Outside explicit `no_bv` spans, `HHA`, `HHB`, `HHF`, `LHA`, `LHB`, and `LHF` each have exactly one note per source melody note.
- For every harmony-enabled melody note, `HHA`, `HHB`, `HHF`, `LHA`, `LHB`, and `LHF` have the same `time` and `duration` as that melody note.
- Every `HHA`, `HHB`, and `HHF` note is higher than the corresponding adjusted `LeadDouble` note.
- Every `LHA`, `LHB`, and `LHF` note is lower than the corresponding adjusted `LeadDouble` note.
- No harmony note uses the same MIDI pitch as the corresponding adjusted `LeadDouble` note.
- `HHA`, `HHB`, `LHA`, and `LHB` substantially follow the melody contour and should not collapse into one repeated pitch for most notes in each measure.
- Every note object has only `name`, `midi`, `time`, and `duration`.
- All MIDI values are within `12..84` (`C0..C6`).
- All times and durations are beat values aligned to the declared grid.
- `tempoMap` and `timeSignatureMap` are preserved from `melody.json`.
- Harmony and pad notes fit the active chord or intentionally rest.
- `python3 script/validate_bv_music.py output/<work_name>` passes, or any remaining soft warnings are musically intentional and should be mentioned in the final response.

If any of these checks fail, especially the melody-index alignment checks for `HHA`, `HHB`, `HHF`, `LHA`, `LHB`, or `LHF`, do not deliver the file. Revise `bv_raw.json` until the contract is satisfied.
