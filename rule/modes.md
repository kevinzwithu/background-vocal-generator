# Background Vocal Generator Project Rules

## Modes

The default mode is `=pt`.

| Mode | Purpose | Load When Executing |
| --- | --- | --- |
| `=pt` | Product, development, documentation, scripting, debugging, and general questions | No project skills |
| `=hx` | Convert `output/<work_name>/chord.json` into `output/<work_name>/note.txt` | `skill/chord-chart/SKILL.md` |
| `=hs` | Generate `output/<work_name>/bv_raw.json` from `melody.json` and `note.txt` | `skill/background-vocals/SKILL.md` |

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

## Examples

Execute:

```text
=hx process output/demo/chord.json
=hx generate a chord chart for demo
=hs process output/demo
=hs generate bv_raw.json from demo melody.json and note.txt
```

Discuss normally:

```text
=hx discuss chord-naming rules
=hs explain the background-vocal track design
```
