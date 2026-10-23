---
name: chord-chart
description: Convert a concrete =hx work's chord.json into note.txt with the deterministic converter, then review contextual chord naming.
---

# Chord Chart

Use after `rule/modes.md` routes an executable `=hx` request or the permitted
same-work chord-chart prerequisite for `=hs`. Apply that file's overwrite policy
before replacing an existing chart; add `--force` only for a replacement permitted
by that policy.

```bash
python3 script/chord_json_to_note.py output/<work_name>/chord.json
```

The converter validates input, groups events, constructs all bars and emits
beat positions, tempo/meter markers and `N.C.`. Review every warning; resolve
ambiguous chord names from the pitch classes and local harmonic context.
Choose readable pop spellings, retaining source spelling where useful. Omit
inversions/bass notation: no slash chords. Do not invent extensions absent
from the source. A genuinely ambiguous name may carry
`(possible alternative: <name>)`.

During naming revisions, preserve bar/beat positions, tempo/meter markers and
`N.C.` placement. `note.txt` contains only the chart, starting with
`Time Unit: beats`; keep each bar's events on one line joined by ` | `.

Confirm successful conversion, complete bar count against the converter summary,
warning disposition and unchanged timing structure after any manual naming edits.
Before delivery, parse the final chart with `script.validate_bv_music.chord_events`
using its `note.txt` path and the source header validated by
`script.chord_json_to_note.read_payload` / `validate_header`; this read-only check
needs no BV output. Apply it also to temporary compatibility output. The parser's
`CHORD_QUALITY_INTERVALS` defines supported qualities. If a name fails, use a
supported equivalent only when it preserves the chord; otherwise report the
unsupported name and downstream blocker without discarding chord tones or
claiming completion. Review alternative labels manually; the parser ignores them.

Report the output link, bar/event counts and material ambiguities concisely.
Offer useful chord-design suggestions separately in chat, with location, proposed
chord and reason; do not force suggestions when none improves the song. Suggestions
must preserve song identity, avoid slash chords and never alter the generated chart.

If valid, accurately representable input fails solely because the converter lacks
a handling rule, read [temporary compatibility](references/temporary-compatibility-patch.md).
Load it only for that failure; ordinary conversions need no additional reference.
