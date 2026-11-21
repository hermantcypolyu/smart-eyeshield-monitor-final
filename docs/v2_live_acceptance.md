# V2 live acceptance protocol

This document is the authoritative procedure for deciding whether
`models/best_v2.pt` may replace `models/best.pt`. It supersedes section 9 of
[domain_v2_finetuning.md](./domain_v2_finetuning.md), which remains the record
of how V2 was trained.

The offline metrics alone do not promote V2. Only a completed live acceptance
run does.

## 1. Current status

| Item | State |
| --- | --- |
| `models/best_v2.pt` installed | yes, Git-ignored |
| `models/best.pt` | still V1, unchanged |
| V2 promoted | **no** |
| Live trials recorded | 0 of 45 |
| Verdict | `V2 NOT APPROVED` |

`experiments/v2_live_acceptance.csv` is committed header-only so the schema is
reviewable. It holds no results and must not hold any until a human performs
the trials.

The overlay timer was corrected before any trial was run. A confirmed incident
now displays `CONFIRMED AFTER: X.XX s` — the frozen violation duration at the
moment of confirmation — instead of the misleading `TIMER: 0.00 s` it showed
previously. See section 6a.

## 2. Why this exists

V2 test metrics, from `domain_v2_test_report.json`, evaluated once:

| Metric | V1 (locked AI-01 row) | V2 |
| --- | --- | --- |
| Precision | 0.8040 | 0.7729 |
| Recall | 0.7993 | 0.7031 |
| F1 | 0.8016 | 0.7364 |
| mAP50 | 0.8439 | 0.7954 |
| mAP50-95 | 0.4867 | 0.4409 |

V2 is behind V1 on every aggregate. It wins only on the `goggles` class
(F1 0.8315 versus the V1 class figure) while `no_goggles` F1 falls to 0.6309
from a stronger V1 baseline. The V2 confusion matrix `[[22,1,193],[1,23,3160],
[0,1,0]]` is laid out **rows = predicted, columns = actual**, confirmed by the
column sums 23 / 25 / 3353. The cell `[0][1] = 1` is therefore one bare-eye
image predicted as `goggles`: a false safe. That single cell is the reason V2
cannot be adopted on the strength of its numbers.

The live run exists to measure the only thing that matters operationally: does
an actual person without goggles ever get reported as `SAFE`.

## 3. Threshold preset A (used for the whole run)

```
candidate-confidence   0.40
goggles-confidence     0.60
no-goggles-confidence  0.55
iou                    0.30
confirmation_seconds   2.0 (default)
```

**NMS IoU semantics, stated correctly.** Lowering the NMS IoU threshold makes
suppression *more* aggressive; raising it keeps *more* boxes. Measured on
`no_goggles_webcam_007.png` at preset A confidences: IoU 0.70 leaves 5
candidates / 3 accepted / 2 duplicates, 0.45 leaves 4 / 2 / 1, 0.40 leaves
3 / 2 / 1, and **0.30 leaves 1 / 1 / 0**. 0.30 is chosen because the duplicate
boxes observed at 0.40 on `_003`, `_007` and `_018` disappear.

Preset B (`0.50 / 0.65 / 0.60`, IoU 0.30) exists only as a documented
alternative. **A run must use a single preset from start to finish.** If the
preset changes, every trial must be redone; a mixed-preset CSV is rejected by
the gate.

IoU 0.25 is the documented fallback if duplicates still appear. It was not
needed and must not be introduced mid-run. Whatever value is used, Ultralytics
NMS remains the only suppression stage: there is no second, application-level
de-duplication pass, and the demo never re-filters accepted boxes.

Never tune thresholds on the locked public test split.

## 4. Trial matrix

| Condition | Pose | Trials |
| --- | --- | --- |
| `goggles` | `front` | 10 |
| `goggles` | `left` | 5 |
| `goggles` | `right` | 5 |
| `no_goggles` | `front` | 10 |
| `no_goggles` | `left` | 5 |
| `no_goggles` | `right` | 5 |
| `empty` | `none` | 5 |

Total: **45 trials**, one person, one scene, one lighting regime per session.
Each trial runs at least 3 seconds so the 2.0 s confirmation window can resolve.

Record every trial, including the ones that fail. Deleting a failure invalidates
the run.

## 5. Per-trial procedure

1. Ask the tool what to do next:

   ```bash
   .venv/bin/python tools/run_v2_live_acceptance.py --next
   ```

   It prints the next trial id, its condition and pose, and the exact demo
   command to run.

2. Run that command. It opens the camera, runs V2, and shows the application
   state and diagnostics in the overlay.

3. Hold the pose for at least 3 seconds. Read off the overlay:

   - final application state (`STATE:`), not the per-frame `RAW:`
   - top accepted class
   - top accepted confidence
   - duplicate accepted boxes
   - application FPS

   For a `no_goggles` trial the `VIOLATION_CONFIRMED` line must show a
   `CONFIRMED AFTER:` value at or above `--confirmation-seconds`. On the formal
   2 s prototype run that means `CONFIRMED AFTER: 2.00 s` or more. Record what
   it actually says; never assume it.

4. Record it:

   ```bash
   .venv/bin/python tools/run_v2_live_acceptance.py --preset A
   ```

   The recorder derives `expected_state`, `false_safe`, `false_alarm` and
   `unknown` itself, hashes the weights file itself, validates the row, prints
   it, and appends only after you type `APPEND`.

Recording is one trial per invocation. There is no batch mode and no `--yes`
flag, by design.

Check progress at any time:

```bash
.venv/bin/python tools/run_v2_live_acceptance.py --status
```

## 6. Definitions

A row is scored from the recorded `condition` and `observed_state`.

**False safe** — condition `no_goggles` and the application reached `SAFE` or
`SAFE_CLEAR_PENDING`. This is the critical failure: a person without eye
protection was told they were compliant. **One false safe rejects the run.**

**False alarm** — condition `goggles` or `empty` and the application reached
`VIOLATION_CONFIRMED`. Annoying rather than dangerous, but too many defeat the
purpose of the device.

**Unknown** — the application never resolved a state during the trial. For
`empty` scenes this is the *correct* outcome.

**Duplicate boxes** — accepted boxes of the same class that still overlap above
IoU 0.30. Measurement only; it never changes the frame decision. A non-zero
count means NMS left stacked detections and is reported, not corrected.

The demo also refuses to start when the weights class map is not exactly
`{0: goggles, 1: no_goggles}`, and it probes row validity before the trial
runs, so a schema failure is discovered before a physical trial is wasted.

## 6a. Reading the overlay (raw vs latched)

The overlay shows two different things and they are allowed to disagree.

`RAW:` is the decision for **this single frame**. It is `UNKNOWN`, `SAFE` or
`VIOLATION`. It has no memory.

`STATE:` is the **latched application state** produced by the temporal filter.
It has memory and is what the trial is scored on.

So `RAW: UNKNOWN` next to `STATE: VIOLATION_CONFIRMED` is not a bug. When the
face leaves the frame the frame decision has nothing to judge, while the
confirmed incident stays latched until it is deliberately cleared. Likewise
`RAW: SAFE` immediately after confirmation shows `STATE: SAFE_CLEAR_PENDING`,
not an instant return to `SAFE`: the safe-clear timer must run to completion
first. **Nothing clears a latched incident except a completed safe-clear
interval.** An incident is never cleared by losing the face, by a dropped
frame, or by the process exiting.

Duplicate nested boxes of the same class never create a second observation,
a second update, or a second incident. One frame produces exactly one
observation and one incident at most.

### Timer meanings

Timing uses only the injected monotonic clock and never wall-clock time or
frame counts, so the numbers are identical no matter what FPS the machine
achieves. Exactly one timer value is meaningful per state:

| `STATE` | Timer line | Meaning |
| --- | --- | --- |
| `UNKNOWN` | `TIMER: IDLE` | No accumulation. Any pending confirmation is still being held within its tolerance. |
| `SAFE` | `TIMER: IDLE` | No incident and no accumulation. |
| `VIOLATION_PENDING` | `CONFIRM TIMER: X.XX / 2.00 s` | Elapsed continuous violation time against the confirmation requirement. |
| `VIOLATION_CONFIRMED` | `CONFIRMED AFTER: X.XX s` | **Frozen.** The violation duration at the instant of confirmation. It does not reset and does not keep counting. |
| `SAFE_CLEAR_PENDING` | `CLEAR TIMER: X.XX / 2.00 s` | Elapsed continuous safe time against the clearance requirement. |

`X.XX` is seconds to two decimals. The requirement shown after the slash comes
from `--confirmation-seconds` or `--clearance-seconds`, whichever applies.

`CONFIRMED AFTER:` is frozen on purpose. A confirmed incident must show the
evidence that the filter really did run, so it can never read `0.00 s` on a
real confirmation. If it ever reads `CONFIRMED AFTER: unknown`, that is a
defect and the trial must be discarded and reported.

Cancellation is explicit, and neither case creates an incident:

- **pending cancelled by UNKNOWN** — no accepted violation frame for longer
  than `--unknown-tolerance-seconds` (default 0.35 s) past the last one;
- **pending cancelled by SAFE** — immediately, on any safe frame.

Interrupting `SAFE_CLEAR_PENDING` with either `VIOLATION` or `UNKNOWN` returns
to `VIOLATION_CONFIRMED` with the original duration preserved; the clear timer
starts again from zero next time.

### Fresh process per trial

Every formal trial starts a **new** application process. Do not reuse a
running demo across trials: incident ids, the latched state and the timers all
carry over, and the recorded `incident id` would no longer identify a single
episode. Close the window, then run the next trial's command.

## 7. Promotion gate

The summariser applies nine checks. All nine must pass.

1. 45 rows, unique trial ids, no gaps in `V2-001` .. `V2-045`.
2. Zero false safes.
3. `goggles` trials reaching `SAFE`: at least 18 of 20.
4. `no_goggles` trials reaching `VIOLATION_CONFIRMED`: at least 18 of 20.
5. `empty` trials staying `UNKNOWN`: 5 of 5.
6. Zero trial crashes, as declared with `--crashes`.
7. Mean application FPS at least 10.
8. Zero unresolved model or class-contract failures, as declared with
   `--contract-failures`.
9. Exactly one weights digest and exactly one threshold preset across the CSV.

Run it with:

```bash
.venv/bin/python tools/summarise_v2_live_acceptance.py
```

Add `--crashes N` and `--contract-failures N` only to state facts observed
during the run. Both default to 0; passing 0 while trials crashed is a false
declaration.

Add `--json` for machine-readable output.

The exit code is 0 only for `V2 ELIGIBLE FOR PROMOTION`, so a scripted check
cannot confuse "the summariser ran" with "V2 passed". Missing rows, one false
safe, or mixed presets all print `V2 NOT APPROVED`.

## 8. Stop rules

Stop the run and treat it as failed if any of these occur:

- a false safe is recorded;
- the threshold preset or the weights file changes mid-run;
- the CSV needs to be edited to fix a row (append a correction note instead, or
  restart the run);
- the camera, lighting or scene changes materially between trials;
- more than 2 of 20 `goggles` trials fail to reach `SAFE`;
- the mean application FPS falls below 10.

Restarting means a fresh CSV. Keep the completed file, do not delete it.

## 9. After the run

If and only if the summariser prints `V2 ELIGIBLE FOR PROMOTION`:

1. Record the verdict and the summariser output in
   `experiments/README.md` context or a new dated entry.
2. Only then consider replacing `models/best.pt`.
3. Update `docs/domain_v2_finetuning.md` to point at the completed run.

Until then `models/best.pt` stays V1. Its SHA-256 must remain
`9366b73b35d4f820af401808aa4e0e58cb29a7b557991b123e5cd6a813fa3591`.

## 10. Out of scope

The acceptance run is a vision check only. It does not cover ESP32 behaviour,
firmware, Blynk, serial transport or the buzzer. Those keep their own
verification. Nothing in this protocol retrains the model or touches the locked
test split.

## 11. Files

| Path | Role |
| --- | --- |
| `tools/live_eye_shield_demo.py` | live demo, now with `--log-csv` and diagnostics |
| `tools/live_eye_shield_demo_v1_backup.py` | frozen pre-change demo |
| `tools/v2_acceptance_schema.py` | shared schema, validation, gate logic |
| `tools/run_v2_live_acceptance.py` | one-trial-at-a-time recorder |
| `tools/summarise_v2_live_acceptance.py` | statistics and promotion verdict |
| `experiments/v2_live_acceptance.csv` | append-only trial log |
| `tests/test_v2_live_acceptance.py` | logic tests, no camera, serial or model |
| `tests/test_live_eye_shield_timer.py` | timer and overlay semantics, injected monotonic clock only |
