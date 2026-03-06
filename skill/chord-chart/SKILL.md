---
name: chord-chart
description: Use in this repository when the user is in =hx chord-chart mode and asks to process a specific work, project, or chord.json file. Runs the deterministic chord-chart converter, reviews naming ambiguities, and provides chord-design suggestions.
---

# Chord Chart

## Purpose

Generate a complete text chord chart from:

```text
output/<work_name>/chord.json
```

and write:

```text
output/<work_name>/note.txt
```

Use this skill only after project mode routing has selected `=hx`.

The deterministic conversion is implemented by:

```text
script/chord_json_to_note.py
```

The agent is responsible for reviewing chord-naming warnings or unsupported
pitch-class sets and for providing musical polishing suggestions. Do not
manually repeat deterministic bar construction when the script can perform it.

## Input Contract

`chord.json` contains:

- `header`
- `chord`

`header` fields:

- `startTime`
- `duration`
- `timeUnit`
- `grid`
- `tempoMap`
- `timeSignatureMap`

All `time` and `duration` values are in beats.

Chord notes usually include `name`, `midi`, `time`, `duration`, `bar`, `beat`, and `grid`. Treat simultaneous notes with the same start time as one chord event.

## Output Contract

Write plain text only. Do not include analysis notes.

Required format:

```text
Time Unit: beats

Bar 1: Time Signature 4/4, BPM 120
Bar 1: beat 1 C
Bar 2: beat 1 Am
Bar 3: beat 1 F | beat 3 G

Bar 9: BPM 96
Bar 9: beat 1 Dm
Bar 10: beat 1 G

Bar 13: Time Signature 3/4
Bar 13: beat 1 C
Bar 14: beat 1 F | beat 2.5 G
```

Rules:

- Output bars in order from the first bar through the final bar implied by `header.duration`.
- Do not omit any bar.
- Mark the first bar with both time signature and BPM.
- Mark later bars only when the time signature or BPM changes at that bar.
- If a bar has multiple chord events, join them on one line with ` | ` and include each beat position.
- If a bar has no chord sounding, write `N.C.`.
- Do not output inversions, bass notes, or slash chords. Write only the chord quality itself.
- Prefer readable pop chord names such as `Ab`, `Cm`, `Db`, `C#dim`, `Fmaj7`, `G7`.
- If a chord quality is genuinely ambiguous, review the script warning, choose the most likely name, and optionally append `(possible alternative: <name>)` to that bar entry.

This contract applies only to `note.txt`. Do not write validation reports, arrangement commentary, or chord-improvement suggestions into `note.txt`.

## Workflow

1. Run the deterministic converter:

   ```bash
   python3 script/chord_json_to_note.py output/<work_name>/chord.json --force
   ```

2. Review every `WARNING` from the converter. Resolve genuinely ambiguous names
   from local harmonic context. The script deliberately ignores inversion and
   bass position.
3. If the converter reports an unsupported pitch-class set, inspect that event,
   determine the intended chord, and complete `note.txt` without weakening the
   input validation.
4. Confirm `note.txt` follows the output contract and that any manual naming
   revision did not alter bar, beat, BPM, time-signature, or `N.C.` placement.
5. In the final chat response, include the validation report and chord-design
   suggestions described below.

The converter owns these deterministic tasks:

- Validate `header.timeUnit`, header maps, duration, and chord-note fields.
- Build bars from `timeSignatureMap`.
- Reject time-signature or BPM changes that cannot be represented accurately.
- Group notes by identical `time` and identify supported pitch-class sets.
- Place events at their bar and beat.
- Emit every bar, including `N.C.` bars.
- Mark BPM and time-signature changes.

## Chord Naming Guidance

- Use enharmonic spellings that fit the local key or surrounding progression.
- For flat-key progressions, prefer `Ab`, `Bb`, `Db`, `Eb`, `Gb`.
- For sharp diminished passing chords, use the spelling that matches the source notes when it is musically clearer, such as `C#dim`.
- Major triad: `C`
- Minor triad: `Cm`
- Diminished triad: `Cdim`
- Dominant seventh: `C7`
- Major seventh: `Cmaj7`
- Minor seventh: `Cm7`
- Minor seventh without fifth: `Cm7(no5)`
- Suspended/add/extended chords are allowed when clearly implied by the pitch classes.

## Validation Checklist

Before finishing:

- `note.txt` exists in the same work directory.
- The converter exits successfully.
- The converter's reported bar count equals the count of `Bar <n>: beat` lines.
- Every converter warning has been reviewed.
- The first line is `Time Unit: beats`.
- Time signature and BPM changes from the header are represented.
- No slash-chord pattern remains in the output.
- Silent gaps are represented as `N.C.` rather than skipped.

## Completion Response

After writing `note.txt`, the final chat response should include:

1. A concise generation result line, for example:

```text
Generated note.txt from chord.json.
```

2. A validation report, for example:

```text
Validation passed:
Time Unit: beats
129 bars from 516 beats in 4/4
129 Bar <n>: beat ... lines
128 source chord event times represented
Empty intro bars filled with N.C.
No slash chords in output
```

3. Chord-design polishing suggestions for the whole song.

These suggestions must not change `note.txt`; they are advisory notes for a producer, arranger, or later manual revision.

Suggestion requirements:

- Include both general full-song observations and specific bar-level suggestions.
- Specific suggestions must name the exact bar, beat if relevant, current chord, proposed chord, and musical reason.
- Prefer practical modern pop/R&B/ballad reharmonization ideas that preserve the song identity.
- Do not suggest slash chords, because this project intentionally omits slash-chord output.
- Do not suggest changes for `N.C.` intro/outro bars unless a musical entrance or transition would clearly benefit.
- Be conservative: suggest a small number of high-value changes instead of rewriting the whole progression.

Suggested format:

```text
Chord-design suggestions:
Overall:
- The progression is stable and hook-friendly, but the repeated loop could use a little more pre-chorus lift before each chorus.
- The diminished passing chords are useful; keep them where they clearly lead into the next harmony.

Specific options:
- Bar 25 beat 3: Eb could become Ebsus4 -> Eb if you want a stronger lift into Bar 26 Ab. This keeps the dominant function but delays the third for more tension.
- Bar 41 beat 3: C#dim works as a leading diminished chord into Bar 42 Ab. Keep it if the melody tolerates E/G/Bb color; otherwise use Eb for a cleaner pop cadence.
- Bar 57 beat 1: Db could become Dbmaj7 if the melody does not clash. This adds a softer pre-rest color without changing the harmonic function.
```
