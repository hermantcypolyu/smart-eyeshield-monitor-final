# Troubleshooting

## Environment

### `ModuleNotFoundError: No module named 'yaml'` (or `cv2`, `serial`, …)

The interpreter running the script is not the project virtual environment.

```bash
.venv/bin/python tools/env_check.py
```

If the dependencies are genuinely missing:

```bash
.venv/bin/python -m pip install -r requirements.txt
```

### `No module named 'torch'` / PyTorch wheel not found

You are probably on Python 3.13+. Check:

```bash
python3 --version
```

PyTorch is pinned to 2.15.0, which has no verified 3.13 wheel/binding path here.
Create the venv with **Python 3.12** explicitly:

```bash
/opt/homebrew/bin/python3.12 -m venv .venv
```

On Windows, install Python 3.12 from python.org and use `py -3.12 -m venv .venv`.

### macOS: `python3` resolves to Anaconda

Anaconda's Python shadows the system interpreter on `PATH`. Prefer an absolute
path when creating the venv (`/opt/homebrew/bin/python3.12` or
`/usr/local/bin/python3.11`) so the environment is reproducible.

## Configuration

### `ConfigError: Config not found: …/config/app.yaml`

`config/app.yaml` is intentionally git-ignored. Create it:

```bash
cp config/app.example.yaml config/app.yaml
```

### `ConfigError: 'filter.violation_ratio' must be between 0.1 and 1.0, got 1.4`

The message names the exact key and the allowed range. Fix the value in
`config/app.yaml`. Bounds exist so that a typo cannot silently disable the
confirmation rule.

### `'filter.window_seconds' must be >= 'filter.confirmation_seconds'`

The rolling window must span the full confirmation period, otherwise the ratio
test cannot observe enough evidence. Keep `window_seconds ≥ confirmation_seconds`.

## Camera

### `could not open camera index 0`

1. Close other applications that may hold the camera (Zoom, Teams, OBS, browser tabs).
2. macOS: **System Settings → Privacy & Security → Camera** — allow Terminal/VS Code.
3. Probe alternatives: `python tools/camera_check.py --list-devices`
4. Try another index: `python tools/camera_check.py --index 1`

### FPS below 15

USB 2.0 hubs, 4K capture requests, and low light all reduce FPS. Lower the
requested resolution in `config/app.yaml` (`camera.width`/`camera.height`), use a
direct USB port, and check that no other app is streaming.

## Git

### `models/best.pt`, `.env`, or `config/app.yaml` appears in `git status`

That would be a secrets/payload leak. Verify:

```bash
git check-ignore -v .env config/app.yaml models/best.pt
```

If nothing is printed, the file is not matched by `.gitignore` — investigate
before committing.

### A `.gitkeep` file is missing after clone

Empty directories are not tracked by Git; `.gitkeep` files exist to preserve them.
If a folder is missing, the clone may be incomplete — re-clone.
