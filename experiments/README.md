# Experiments

Raw and summarised results. **Every number reported anywhere in this project must
be traceable to a row in one of these files**, together with the conditions it was
measured under ([plan.md §12](../plan.md#12-formal-test-matrix)).

## Files

| File | Contents | Phase |
|---|---|---|
| `ai_metrics.csv` | Model evaluation metrics per run (precision, recall, F1, mAP) | 2, 8 |
| `system_trials.csv` | End-to-end trial rows incl. per-stage latency timestamps | 7, 8 |

Both files currently contain **headers only**. They are empty because no
measurement has been performed. Nothing in the repository claims otherwise.

## Rules

1. **Append, never overwrite.** A superseded run keeps its row; add a note.
2. **No editing of recorded numbers.** If a trial was invalid, add a corrected row
   and mark the original `result=invalid` with a reason.
3. **Record the conditions.** Model name/version, dataset split, lighting, pose,
   distance, hardware, and OS belong in `notes` or in the columns provided.
4. **Report spread, not just the mean.** Mean, standard deviation, minimum,
   maximum, and success rate where applicable (plan §11 Phase 8).
5. **Keep failures.** Failed and partial trials stay in the record. Removing them
   is misrepresentation.
6. **Targets are intentions, not results.** Values in the plan's "pass intention"
   column must never be copied into a results column.

## Column reference — `ai_metrics.csv`

| Column | Meaning |
|---|---|
| `test_id` | Matrix ID (`AI-01` … `AI-05`) |
| `model` | Architecture and weights identifier |
| `dataset` | Dataset name/version |
| `split` | `val` or `test` (test set is locked in Phase 1) |
| `train_images` / `val_images` / `test_images` | Actual counts used |
| `precision`, `recall`, `f1`, `map50`, `map50_95` | Measured values only |
| `notes` | Conditions, caveats, subgroup definition |
| `recorded_at` | ISO-8601 timestamp of the run |

## Column reference — `system_trials.csv`

| Column | Meaning |
|---|---|
| `trial_id` | Unique per trial; used to group repeated runs |
| `scenario_id` | Matrix ID (`SYS-01`, `COM-02`, `IOT-01`, `ROB-01`, …) |
| `violation_start_ts` | First frame where the violating observation begins |
| `confirmed_ts` | Temporal filter declares persistence satisfied |
| `serial_sent_ts` | Message handed to the serial transport |
| `esp32_ack_ts` | Acknowledgement received from the ESP32 |
| `alarm_active_ts` | Alarm physically active |
| `total_latency_ms` | `alarm_active_ts − violation_start_ts` |
| `result` | `pass` / `fail` / `invalid` (with reason in `notes`) |
| `recorded_at` | ISO-8601 timestamp of the run |

Timestamps must come from a monotonic clock within one process, or be converted to a
single reference clock, so that a latency cannot be inflated or deflated by a clock
domain change.