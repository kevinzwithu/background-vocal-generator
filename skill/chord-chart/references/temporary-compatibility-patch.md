# Temporary Compatibility Patch

Use only after an observed converter failure on valid input that can be accurately
represented in `note.txt`: for example a missing chord template or harmless numeric
noise. Never weaken validation, discard source data, guess unsupported chords, or
use this fallback for malformed input, mid-bar tempo/meter changes, resource limits
or filesystem failures.

1. Capture the original error. Preserve exact pre-task bytes, hashes and repository
   state of every converter file to be patched, including existing user edits.
2. Change only the minimum existing converter code required for the observed
   limitation. Do not modify source input, tests, configuration, rules, skills,
   documentation or unrelated code.
3. Convert and check the chart. Whether successful or not, restore every patched
   file byte-for-byte and verify hashes and repository state. Never use Git restore
   operations that could discard pre-existing edits. Stop if restoration is unverified.

Only the requested `note.txt` may persistently change. Report the limitation,
conversion outcome and verified restoration. Provide a concise issue draft for
`kevinzwithu/background-vocal-generator`: imperative title, exact reproduction/input
location, actual vs expected behavior, restored workaround, proposed fix and
regression acceptance criteria. Draft only; do not publish the issue automatically.
