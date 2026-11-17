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

---

## Phase 1 audit findings

The source export was audited read-only; see `docs/dataset_audit.md` for the full
report and `data/reviewed/audit/` for the 13 machine-readable records.

**Verdict: `rejected_for_training`.** The archive is a 5-class general-PPE export
(`boots`, `glasses`, `gloves`, `helmet`, `vest`) from workspace
`eie3360-fwbnu`, not the 2-class `goggles`/`no_goggles` scheme this project
requires. Neither required class exists in the export, and `glasses` cannot be
renamed to `goggles`: its median box covers 0.6 % of the frame with aspect ratio
1.86, i.e. product cut-outs of eyewear rather than worn shields.

Measured: 400 images, 400 label files, 770 instances, 0 corrupt images, 0 exact
duplicates, 377 near-duplicate pairs (video frames), 59 split-leakage groups, 2
malformed polygon rows, 1 empty label file. Splits are 75.25/20.50/4.25 %, against
a ~10 % test target.

The source ZIP is untouched — SHA-256
`39afc3ac747c281e6165732f82f512fb6fcbbe15f0a0e7d58015d901c3e2cf53` before and
after. No dataset content was added to version control; only the audit records
and the split manifest are tracked.

The `splits/data.yaml` and `splits/split_manifest.csv` described above remain
Phase 0 placeholders and have **not** been populated from this export, because
the export was rejected.
