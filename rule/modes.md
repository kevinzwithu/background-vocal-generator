# Background Vocal Generator Project Rules

## Modes

The default mode is `=pt`.

| Mode | Purpose | Load When Executing |
| --- | --- | --- |
| `=pt` | Product, development, documentation, scripting, debugging, and general questions | No project skills |
| `=hx` | Convert `output/<work_name>/chord.json` into `output/<work_name>/note.txt` | `skill/chord-chart/SKILL.md` |
| `=hs` | Author a v1 `bv_plan.json`, render and score `bv_raw.json`; select v2 diagnostics explicitly | `skill/background-vocals/SKILL.md` |

New `=hs` work defaults to v1 plans and scoring. An explicit
`--evaluation-version v2` request selects v2 plans, diagnostics and independent
music review. The validator uses the same flag and defaults to v1 scoring.

## Routing Rules

Execute an `=hx` or `=hs` task and load its skill only when all of these are
true:

- The user selected the corresponding mode.
- The user provided a concrete work, file, directory, or output target.
- The user expressed execution intent in any language, such as process,
  generate, organize, run, or execute.

If the user is only discussing requirements, rules, documentation, prompts,
product design, or implementation details, handle the request as `=pt` and do
not load the mode skill, even when the message contains `=hx` or `=hs`.

## Chart overwrite policy for `=hx` and `=hs`

Create a missing `note.txt` without prompting. Preserve existing manual chart
edits; if regeneration would overwrite them and replacement is not already
authorized, ask only about that overwrite and continue independent source
inspection. Use `--force` only when deliberately replacing a chart under this
policy; existing authorization does not require another confirmation.

## Chord-chart prerequisite for `=hs`

An executable `=hs` request also authorizes the chord-chart workflow for the same
work when `note.txt` is missing or demonstrably stale and valid `chord.json` is
available. Load `skill/chord-chart/SKILL.md` for that prerequisite without asking
the user to select `=hx` again. Reuse a chart whose content matches the current
source; confirm staleness by comparing chart content with the source.

This prerequisite may persistently create or refresh that work's `note.txt`.
Apply the shared chart overwrite policy above. If required source data is missing
or invalid, report the specific missing prerequisite; do not invent a chart or
keep retrying.
The chord-chart skill's temporary compatibility procedure remains applicable
only under its stated conditions. This exception does not authorize persistent
changes to source data, converter code, other works or unrelated files.

## Examples

Execute:

```text
=hx process output/demo/chord.json
=hx generate a chord chart for demo
=hs process output/demo
=hs generate bv_plan.json and bv_raw.json from demo melody.json and note.txt
```

Discuss normally:

```text
=hx discuss chord-naming rules
=hs explain the background-vocal track design
```
