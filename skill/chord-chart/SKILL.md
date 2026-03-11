---
name: chord-chart
description: Use in this repository after =hx routing selects a concrete chord-chart task. Run the deterministic converter, review chord-naming ambiguities, validate note.txt, and provide concise chord-design suggestions.
---

# Chord Chart

## Purpose

Convert:

```text
output/<work_name>/chord.json
```

into:

```text
output/<work_name>/note.txt
```

Use this skill only after `rule/modes.md` selects an executable `=hx` task.

The deterministic converter is:

```text
script/chord_json_to_note.py
```

Use it for bar construction and validation. The agent reviews warnings,
resolves chord-naming ambiguity from musical context, validates the result, and
provides advisory chord-design suggestions.

## Input Contract

`chord.json` contains `header` and `chord`.

The header includes `startTime`, `duration`, `timeUnit`, `grid`, `tempoMap`, and
`timeSignatureMap`. All `time` and `duration` values are in beats.

Treat simultaneous notes with the same start time as one chord event.

## Output Contract

Write plain text only to `note.txt`; do not include validation reports,
commentary, or suggestions in the file.

Required shape:

```text
Time Unit: beats

Bar 1: Time Signature 4/4, BPM 120
Bar 1: beat 1 C
Bar 2: beat 1 Am
Bar 3: beat 1 F | beat 3 G

Bar 9: BPM 96
Bar 9: beat 1 N.C.
```

Rules:

- Output every bar in order through the final bar implied by `header.duration`.
- Mark the first bar with time signature and BPM; mark later changes only at
  the bar where they occur.
- Put multiple chord events for one bar on one line, joined with ` | `.
- Write `N.C.` when no chord sounds.
- Omit inversions and bass notes; never output slash chords.
- Prefer readable pop names such as `Ab`, `Cm`, `Db`, `C#dim`, `Fmaj7`, and
  `G7`.
- For a genuinely ambiguous quality, choose the most likely contextual name and
  optionally append `(possible alternative: <name>)`.

## Workflow

1. Run:

   ```bash
   python3 script/chord_json_to_note.py output/<work_name>/chord.json --force
   ```

2. Review every converter `WARNING` and resolve genuine naming ambiguity from
   local harmonic context. The converter deliberately ignores inversion and
   bass position.
3. If valid input fails only because the converter lacks a local handling rule,
   read and follow
   `references/temporary-compatibility-patch.md`. Do not read or apply that
   policy during a normal successful conversion.
4. Confirm `note.txt` satisfies the output contract without altering its bar,
   beat, BPM, time-signature, or `N.C.` placement during naming revisions.
5. Report validation results and concise chord-design suggestions in chat.

The converter owns input validation, bar construction, event grouping,
supported pitch-class identification, event placement, complete bar emission,
and BPM/time-signature markers. Do not manually repeat those tasks when the
converter can perform them.

## Chord Naming

- Choose enharmonic spellings that fit the local key and progression.
- In flat keys prefer `Ab`, `Bb`, `Db`, `Eb`, and `Gb`.
- Use the source spelling when it clarifies a sharp diminished passing chord,
  such as `C#dim`.
- Common forms include `C`, `Cm`, `Cdim`, `C7`, `Cmaj7`, `Cm7`, and
  `Cm7(no5)`.
- Use suspended, added, or extended names only when clearly implied by the
  pitch classes.

## Completion Gate

Before finishing, confirm:

- `note.txt` exists beside the input and the converter exited successfully.
- The reported bar count equals the count of `Bar <n>: beat` lines.
- Every warning was reviewed.
- The first line is `Time Unit: beats`.
- BPM and time-signature changes are represented.
- Every bar is present, silent gaps use `N.C.`, and no slash chord remains.
- If the temporary compatibility policy was used, all patched files were
  restored byte-for-byte and restoration was verified.

## Completion Response

Include:

1. A concise generation result.
2. A validation summary with bar count, represented source event count, warning
   disposition, `N.C.` handling, and slash-chord check.
3. A small number of conservative, high-value chord-design suggestions.
4. Only when a temporary compatibility patch was used, the required patch
   report and repository-ready GitHub issue from the reference policy.

Suggestions are advisory and must not change `note.txt`. Include both an overall
observation and specific options that name the exact bar, beat when relevant,
current chord, proposed chord, and musical reason. Prefer practical modern
pop/R&B/ballad ideas that preserve the song identity, do not suggest slash
chords, and avoid changing `N.C.` intro/outro bars without a clear musical
benefit.
