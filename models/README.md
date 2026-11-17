# Models directory

## `best.pt`

The trained YOLOv8n weights are **not committed** (large binary; `.gitignore`
excludes `models/*.pt`). Copy or symlink the file produced by the training run:

```bash
cp training/runs/detect/train/weights/best.pt models/best.pt
```

The path is configurable via `model.path` in `config/app.yaml`.

## Model contract

The application requires the following from any weights placed here. Violating
this contract inverts safety decisions, so it is checked at load time (Phase 3).

| Property | Required value |
|---|---|
| Class 0 | `goggles` — compliance: safe |
| Class 1 | `no_goggles` — compliance: violation |
| Task | Detection (`detect`), single-stage |
| Architecture | YOLOv8n unless a measured comparison justifies otherwise |

The class map lives in [config/classes.yaml](../config/classes.yaml). It is the
single source of truth; do not duplicate class names elsewhere.

## Not yet present

Phase 0 ships this folder with documentation only. `models/best.pt` appears after
Phase 2. Any run before then should be assumed to be using a mock detector.
