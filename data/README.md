# Data directory

This folder holds dataset media and its **non-media** records. Media never enters Git.

| Path | Contents | Committed? |
|---|---|---|
| `raw/` | Untouched source exports (Roboflow export, phone camera frames) | No — images/archives ignored |
| `reviewed/` | Relabelled/cleaned images in the final two-class scheme | No |
| `splits/` | Split manifest CSV, `data.yaml` for training, statistics | Yes — `.csv`, `.json`, `.yaml` kept |
| `test_cases/` | Consented, non-private recorded clips and stills for deterministic tests | No |

## Rules

1. **Never commit images, video, or dataset archives.** Media extensions are ignored
   globally in `.gitignore`.
2. **Provenance is mandatory.** Every source added under `raw/` gets an entry in
   `docs/annotation_guideline.md` (source URL, licence, date, what was altered).
3. **The test split is locked.** Once a file appears in `splits/test_manifest.csv`,
   it may not be moved to training, used for threshold selection, or used to pick
   epochs.
4. **Split by recording session, not by frame.** Adjacent frames from one clip must
   never straddle train/test.
5. **Consent first.** Self-collected data from participants is only stored where
   written consent exists and the collection purpose is documented.

## Expected layout after Phase 1

```text
splits/
├── split_manifest.csv    # image_id, source, session_id, labels, split, lighting, pose, goggles_type
├── data.yaml             # Ultralytics dataset descriptor (train/val/test paths, names)
└── statistics.json       # class counts per split, produced by tools/inspect_dataset.py
```

Phase 0 creates only the folders. No dataset content exists yet, and no class
counts are claimed.
