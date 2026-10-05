# New user feedback after a closed experimental candidate

The ordinary composer can start a **new normal repair phase** from a complete,
closed saved experimental candidate. It does not resume the experiment, clear its
marker, retry an unfinished schedule, or treat failed model output as findings.

## Admission and lineage

- The project view supplies `repair_from`, pinning the displayed durable candidate
  record/hash/revision/fingerprint, canonical base, complete schedule/compiler
  lineage, checker, and harness policy. The composer sends this with its normal
  text through either HTTP or native transport. Missing or stale pins fail before
  candidate creation and provider dispatch; no internal stage name is user input.
- Existing recheck, ready-state, complete-unit, partition, checkpoint, and
  experimental lineage checks run against the unmodified durable predecessor.
  Canonical questions, archived/disabled projects, open or active attempts,
  incomplete candidates, missing terminal receipts, and stale state are rejected.
- One admission invariant is rerun under the candidate writer transaction before
  insertion. `repair_phase` records the exact predecessor, old experiment and
  each terminal receipt hash. The old record remains unchanged and becomes
  immutable through candidate writes. Its five completed checkpoints, failed
  review attempts and raw output remain available under their original identities.
- The initial candidate is the exact staged predecessor plus one new user source.
  `prior_work_units` preserves completed intent/checkpoints. Later saves cannot
  remove/change admission, rewrite those original sources/checkpoints, or reopen
  the old experiment. User feedback may describe tester findings, but is explicitly
  an instruction source, not a semantic review certificate.
- Only replay-validated prior held review findings may become an automatic repair
  agenda. Invalid/raw output remains audit-only. Fresh checks still govern apply.

## Existing execution and limits

This enters the ordinary router, scheduler, unit-scoped schema, atomic compiler,
harness, and apply transaction. No alternative generation pipeline or provider
configuration is introduced. The new phase has the existing ordinary ceiling:
5 calls, 163,840 input bytes per request, 393,216 shared input bytes, 98,304 output
bytes per request, and 180 seconds per call. The old stage's spent budget remains
closed; it is never reset. The scheduler reserves review capacity and may stop
with the candidate preserved if the new work cannot fit.

## Offline verification

`tests/test_complete_candidate_repair.py` freezes the complete candidate and
closed invalid-output review from the saved recovery snapshot. Provenance lists
all six original snapshot hashes. Fake-provider tests cover ordinary generation,
unit schema projection, fresh harness/apply, immutable history, invalid-output
isolation, stale tabs, writer races, provenance corruption, HTTP and native
submission. Mock acceptance demonstrates plumbing, not model repair quality.

The separate exact-input offline capture for the intended tester feedback measured
an initial router request of 76,917 bytes, leaving 316,299 shared input bytes and
four calls including review. Later unit count and request sizes depend on the
unknown router manifest and generated content. This measurement is not a promise
that a complete real repair fits. No real provider or paid call was made.
