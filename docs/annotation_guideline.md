# Annotation guideline (draft — to be completed in Phase 1)

Status: **not yet approved**. Phase 1 must finish and review this document before
any training image is annotated. It is seeded here so the two-class scheme cannot
drift while tooling is being built.

## 1. Class definitions

Use exactly two primary classes.

### `goggles` (class 0 — safe)

A visible person is **correctly wearing genuine laboratory or industrial safety
goggles over the eyes**.

Qualifies:
- Sealed or semi-sealed safety goggles covering the eyes.
- Over-glasses goggles worn over prescription spectacles.
- Tinted safety goggles (anti-laser wavelength-specific goggles count).

Does **not** qualify (label as `no_goggles`):
- Ordinary prescription glasses.
- Sunglasses.
- Goggles resting on the forehead, hanging around the neck, or over one eye.
- Face shields alone, with eyes uncovered.

### `no_goggles` (class 1 — violation)

The relevant face or head region is **sufficiently visible for assessment**, but
genuine safety goggles are **absent or incorrectly positioned**.

## 2. Annotation unit

Preferred: one bounding box around the **visible head/face region**, identical in
semantic unit for both classes.

Forbidden: a tight goggles-only box for `goggles` combined with a whole-head box for
`no_goggles`. Mixed units make the model's decision boundary meaningless and are
grounds for rejecting the annotation set.

## 3. When to draw nothing (→ `UNKNOWN` at runtime)

Draw **no box** and record why in the review log when:
- The face is smaller than the minimum assessment size (to be defined numerically
  in Phase 1, tied to `observation.minimum_box_area_ratio`).
- The head is fully turned away, or occluded such that eyes are not visible.
- The image is too blurred, over/under-exposed, or too dark to judge.
- The subject is a poster, screen, reflection, or mannequin.

**A missing box is never a `no_goggles` label.** This is the central rule of the
project (see `plan.md §1.2`): failing to detect goggles does not prove a violation.

## 4. Hard cases — rules to finalise in Phase 1

| Case | Provisional rule |
|---|---|
| Side profile | Label if eyes visible enough to judge; otherwise no box |
| Goggles on forehead | `no_goggles` |
| One eye covered | `no_goggles` |
| Mask covering nose/mouth only | Judge on eye coverage alone |
| Very small / distant face | No box |
| Multiple people | One box per assessable head |
| Blurred motion | No box if eye coverage cannot be determined |

## 5. Consistency requirements

- Two annotators label a shared 10% sample; disagreement is reviewed before mass
  labelling.
- Every retained annotation is reviewed against this document.
- Duplicate/near-duplicate frames are removed **before** splitting (Phase 1 tooling).

## 6. Data sources and provenance

| Source | Licence | Added | Notes |
|---|---|---|---|
| _(to be filled in Phase 1)_ | | | |

No third-party or personal data is committed to this repository. Consent and
collection purpose for self-collected data are recorded here once collection begins.
