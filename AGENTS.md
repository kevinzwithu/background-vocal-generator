# Background Vocal Generator Agent Instructions

## Always Read Project Rules

At the start of work in this repository, read and follow:

```text
rule/modes.md
```

That file is the single source of truth for the default mode, mode routing,
execution intent, and skill loading.

## Script Layout

Keep `script/` limited to `bv_raw_to_midi.py`, `chord_json_to_note.py`,
`midi_to_json.py`, `validate_bv_music.py`, and `project_common.py`.
Extend these modules for background-vocal work. BV plan rendering and MIDI
export belong in `bv_raw_to_midi.py`; music diagnostics and reports belong in
`validate_bv_music.py`. Put shared timeline helpers and limits in `project_common.py`.

## Stable Fallback

If a future agent does not appear to have loaded these instructions, the user
can paste one of these commands:

```text
Read AGENTS.md and follow the project mode rules.
=hx process output/<work_name>/chord.json
=hs process output/<work_name>
```
