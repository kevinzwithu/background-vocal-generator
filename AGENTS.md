# Background Vocal Generator Agent Instructions

## Always Read Project Rules

At the start of work in this repository, read and follow:

```text
rule/modes.md
```

That file is the single source of truth for the default mode, mode routing,
execution intent, and skill loading.

## Stable Fallback

If a future agent does not appear to have loaded these instructions, the user
can paste one of these commands:

```text
Read AGENTS.md and follow the project mode rules.
=hx process output/<work_name>/chord.json
=hs process output/<work_name>
```
