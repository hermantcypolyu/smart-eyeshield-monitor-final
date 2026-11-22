# RECOVERY GUIDE — Smart Eye-Shield Monitor

How to get back to the frozen working build if later changes break it.

- **Release branch:** `release/phase4-working`
- **Release tag:** `v0.9-working-demo`
- **Frozen commit:** `ec5241cb22c0de8da39fabe6795842f1c1d8b946`

Work from the repository root throughout:

```bash
cd /Users/hermanyeung/Downloads/AIOT-MiniProject
```

---

## 1. Check what broke first

```bash
git status
git --no-pager diff --stat
git --no-pager log --oneline -10
git tag --list
```

Write down the current HEAD before you change anything, so you can return to the
broken state if you need to diagnose it:

```bash
git rev-parse HEAD
```

---

## 2. Quick inspection without changing anything

Look at the frozen snapshot read-only. Nothing is modified and no branch is
created, so this is always safe:

```bash
git checkout --detach v0.9-working-demo
```

Return to where you were:

```bash
git switch master          # or whichever branch you came from
```

---

## 3. Restore the release branch

Use this when you want a normal, working branch that points at the release.

```bash
git switch release/phase4-working
```

If the branch already exists locally with unwanted commits on it, reset it back
to the tag:

```bash
git switch release/phase4-working
git reset --hard v0.9-working-demo
```

> `git reset --hard` discards uncommitted work in that branch. Commit or stash
> anything you want to keep (`git stash push -u -m "wip"`) before running it.

If the local branch is damaged or missing, recreate it from the tag:

```bash
git switch -c release/phase4-working v0.9-working-demo
# if it exists and is wrong:  git branch -f release/phase4-working v0.9-working-demo
```

---

## 4. Restore master to the frozen commit

`master` is the development line and will move on. To compare against, or
temporarily return to, the frozen point:

```bash
git switch master
git --no-pager log -1            # note the current hash first
git reset --hard ec5241cb22c0de8da39fabe6795842f1c1d8b946
```

Safer alternative if `master` has work you may want later — branch the recovery
off the tag instead of rewinding:

```bash
git switch -c recover/from-v0.9 v0.9-working-demo
```

### Recover a file you accidentally broke

Restore a single file from the frozen snapshot instead of the whole tree:

```bash
git checkout v0.9-working-demo -- tools/incident_lifecycle.py
git checkout v0.9-working-demo -- firmware/esp32_eye_shield_monitor/esp32_eye_shield_monitor.ino
git checkout v0.9-working-demo -- tools/live_eye_shield_demo.py
git checkout v0.9-working-demo -- tests/test_incident_lifecycle.py
```

### Something was committed that should not have been

```bash
git --no-pager log --oneline v0.9-working-demo..HEAD   # see what to undo
git revert <bad-commit>                                # safe, keeps history
```

Prefer `git revert` over rewinding public history (`git reset` on a pushed
branch rewrites it for everyone).

---

## 5. Clean build procedure

The Python side has no build step, but stale caches and a broken venv are the
two most common causes of "it worked yesterday".

```bash
cd /Users/hermanyeung/Downloads/AIOT-MiniProject

# 1. Clear Python caches
find . -type d -name __pycache__ -not -path './.venv/*' -prune -exec rm -rf {} +
rm -rf .pytest_cache

# 2. Recreate the virtual environment from the pinned dependency list
rm -rf .venv
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

# 3. Verify the environment (models, camera, serial, packages)
.venv/bin/python tools/env_check.py

# 4. Verify the code
.venv/bin/python -m pytest -q
```

Expected test result at the frozen commit: **381 passed**.

Model weights are not in Git. If `models/best.pt` or `models/best_v2.pt` is
missing, restore it from your separate model backup (see
[BACKUP_INVENTORY.md](./BACKUP_INVENTORY.md)), then verify:

```bash
shasum -a 256 models/best.pt models/best_v2.pt
```

| File | Expected SHA-256 |
|---|---|
| `models/best.pt` | `9366b73b35d4f820af401808aa4e0e58cb29a7b557991b123e5cd6a813fa3591` |
| `models/best_v2.pt` | `fec02c88f4236d85d4e913df57638a96cf0a35498f6a65c413b7fe14077f22fc` |

---

## 6. Firmware upload procedure

```bash
cd /Users/hermanyeung/Downloads/AIOT-MiniProject

arduino-cli compile \
  --fqbn esp32:esp32:esp32s3 \
  firmware/esp32_eye_shield_monitor

arduino-cli upload \
  -p /dev/cu.usbserial-10 \
  --fqbn esp32:esp32:esp32s3 \
  firmware/esp32_eye_shield_monitor
```

Verify the port before uploading — the ESP32-S3 appears as
`/dev/cu.usbserial-10`, and the Arduino IDE likes to pick
`/dev/cu.usbserial-110`, which is usually a different device:

```bash
ls /dev/cu.*
```

Confirm you have the right board:

```bash
arduino-cli board list
```

### Upload troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Invalid FQBN: not an FQBN: 115200` | `-b 115200` was passed to `arduino-cli upload` | The baud flag belongs to the **serial monitor**, not the uploader. Drop `-b` and keep `--fqbn esp32:esp32:esp32s3`. |
| `Could not open /dev/cu.usbserial-110 ... No such file or directory` | Wrong port | Use `/dev/cu.usbserial-10`, or whatever `arduino-cli board list` reports. |
| `Resource busy: '/dev/cu.usbserial-10'` | The demo or Arduino Serial Monitor still holds the port | Quit the demo, close the Serial Monitor, then retry. Find the holder with `lsof /dev/cu.usbserial-10`. |
| `Unable to verify flash chip connection` / `No more data to read from the serial port` at 921600 baud | Link instability at the stub's high baud rate | Retry at 115200: `arduino-cli upload -p /dev/cu.usbserial-10 --fqbn esp32:esp32:esp32s3 --baud 115200 firmware/esp32_eye_shield_monitor`. If it still fails: use a known-good data USB cable, a different USB port, unplug/replug, and retry. |
| Board has no BOOT button | The shield exposes no BOOT switch | Not needed. The ESP32-S3 auto-resets through RTS/DTR. Only if the board is wedged: unplug, hold nothing, replug while the uploader is already waiting. |

### Verify the firmware after flashing

```bash
.venv/bin/python tools/test_esp32_quick.py
```

This script has the port (`/dev/cu.usbserial-10`) hard-coded and takes no
arguments. It sends `STATUS`, `ALARM` (red + buzzer for 3 s) and `SAFE`, and
should end with `通信成功！ESP32 正常工作。`

The `STATUS` reply includes the banner line
`firmware : Phase 4A incident lifecycle + bring-up presets`, which confirms the
right build is on the board.

For an interactive shell instead, use the argument-taking probe:

```bash
.venv/bin/python tools/test_esp32_serial.py --port /dev/cu.usbserial-10
```

---

## 7. Python launch procedure

Make sure no other process holds the serial port:

```bash
lsof /dev/cu.usbserial-10     # expect no output when free
```

Run the demo:

```bash
.venv/bin/python tools/live_eye_shield_demo.py \
  --source 0 \
  --weights models/best_v2.pt \
  --candidate-confidence 0.40 \
  --goggles-confidence 0.60 \
  --no-goggles-confidence 0.55 \
  --iou 0.30 \
  --device auto \
  --serial-port /dev/cu.usbserial-10
```

Keys: `A` acknowledge · `R` reset · `Q` quit.

Run without hardware (no serial) by omitting `--serial-port`.

Run the dashboard:

```bash
bash dashboard/start.sh
```

Diagnostic lines to watch for in the console, which show whether the board or the
laptop is the one driving the state:

- `[LIFECYCLE STATE] <from> -> <to>`
- `[LIFECYCLE TX] Sending command: <command>`
- `[ESP32 RX] <board reply>`

If `LIFECYCLE TX` and `ESP32 RX` disagree with the LEDs, the desynchronisation
bug (`B-01`) is still present.

---

## 8. Restore checklist

Run these in order and stop at the first failure:

```bash
# 1. Confirm you are on the release
git switch release/phase4-working
git --no-pager log -1

# 2. Confirm the tree is clean
git status --short            # expect no tracked modifications

# 3. Confirm the models are intact
shasum -a 256 models/best.pt models/best_v2.pt

# 4. Confirm the environment
.venv/bin/python tools/env_check.py

# 5. Confirm the code
.venv/bin/python -m pytest -q          # expect 381 passed

# 6. Confirm the board link
.venv/bin/python tools/test_esp32_quick.py

# 7. Run the demo
.venv/bin/python tools/live_eye_shield_demo.py \
  --source 0 --weights models/best_v2.pt \
  --candidate-confidence 0.40 --goggles-confidence 0.60 \
  --no-goggles-confidence 0.55 --iou 0.30 --device auto \
  --serial-port /dev/cu.usbserial-10
```

Related documents: [FINAL_WORKING_VERSION.md](./FINAL_WORKING_VERSION.md),
[SYSTEM_STATE_REFERENCE.md](./SYSTEM_STATE_REFERENCE.md),
[BACKUP_INVENTORY.md](./BACKUP_INVENTORY.md).
