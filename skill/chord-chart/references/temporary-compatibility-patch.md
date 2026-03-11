# Temporary Compatibility Patch

Read this file only when valid input can be represented accurately in
`note.txt`, but `script/chord_json_to_note.py` fails because it lacks a local
handling rule.

## Eligibility

Eligible examples:

- A common chord, omitted-note chord, alias, or enharmonic naming rule missing
  from current templates.
- A valid partial chord identifiable reliably from local harmonic context.
- A valid representation, fractional beat, time signature, or formatting case
  that can be represented losslessly.
- Harmless floating-point noise rejecting a genuinely bar-aligned event.

Do not apply this policy to malformed JSON, missing header data, invalid or
out-of-range values, genuinely mid-bar time-signature or BPM changes, chords
requiring unsupported guesswork, resource-limit violations, filesystem
failures, weakened validation, or discarded source information.

## Procedure

1. Run the unmodified converter and capture the exact failure.
2. Preserve the exact original bytes, repository state, and content hash of
   every file that may be patched. Do not rely on `git checkout`, `git restore`,
   or another operation that could destroy pre-existing user changes.
3. Modify only the minimum existing converter code needed for the observed
   limitation. Do not change input, tests, configuration, rules, skills,
   documentation, unrelated code, validation, or safety limits, and do not
   silently ignore input.
4. Run the patched converter and validate `note.txt`.
5. Whether conversion succeeds or fails, restore every patched file to its
   exact pre-task bytes and verify its hash and repository state.
6. If restoration cannot be verified, stop and report the failure.

The only persistent file created or changed by this fallback is the requested
`output/<work_name>/note.txt`.

## Completion Report

State:

```text
Temporary compatibility patch used: yes
Patched limitation: <specific limitation>
Generated note.txt successfully
Repository code restored to its pre-task state: verified
```

Also provide one repository-ready issue based only on the observed failure. Use
an imperative title matching the issue type, such as `support`, `accept`, `fix`,
or `tolerate`.

```markdown
GitHub issue to file

Repository: kevinzwithu/background-vocal-generator
Title: chord-chart: <imperative summary of the observed limitation>

## Summary
<Concise description of the missing or incorrect behavior.>

## Reproduction
- Input work: <work name>
- Location: <bar and beat or other exact location>
- Relevant input: <pitch-class set or exact valid input detail>
- Command: `python3 script/chord_json_to_note.py ... --force`

## Actual behavior
<Original converter error or incorrect output.>

## Expected behavior
<Accurate expected behavior.>

## Temporary workaround used
<Minimal temporary change used for this task; state that it was restored.>

## Proposed permanent fix
<Scoped implementation recommendation.>

## Acceptance criteria
- The reported valid input is handled accurately.
- Existing validation and safety limits remain intact.
- A regression test covers the case.
- The converter succeeds without a temporary patch.
```
