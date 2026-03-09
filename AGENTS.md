# Background Vocal Generator Agent Instructions

## Always Read Project Rules

At the start of work in this repository, read and follow:

```text
rule/modes.md
```

This file defines the project conversation modes and how to route requests.

## Modes

The default mode is:

```text
=pt
```

`=pt` is normal mode. In this mode, do not automatically load the chord-chart or background-vocals skills.

## Chord Chart Mode

When the user switches to:

```text
=hx
```

read and follow:

```text
skill/chord-chart/SKILL.md
```

Use this mode to convert:

```text
output/<work_name>/chord.json
```

into:

```text
output/<work_name>/note.txt
```

If the user provides a concrete file, work name, or execution-oriented request in `=hx` mode, start the chord-chart task directly. If the user is only discussing requirements, prompts, rules, or implementation details, handle the request in normal mode.

## Background Vocals Mode

When the user switches to:

```text
=hs
```

read and follow:

```text
skill/background-vocals/SKILL.md
```

Use this mode to convert:

```text
output/<work_name>/melody.json
output/<work_name>/note.txt
```

into:

```text
output/<work_name>/bv_raw.json
```

If the user provides a concrete file, work name, or execution-oriented request in `=hs` mode, start the background-vocals task directly. If the user is only discussing requirements, prompts, rules, or implementation details, handle the request in normal mode.

## Stable Fallback

If a future agent does not appear to have loaded these instructions, the user can paste one of these commands:

```text
Read AGENTS.md and follow the project mode rules.
=hx process output/<work_name>/chord.json
=hs process output/<work_name>
```

