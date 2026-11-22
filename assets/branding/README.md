# Branding assets

The header shows two institution marks, both optional:

| File | Position | Notes |
| --- | --- | --- |
| `polyu_logo.png` | top left | PolyU mark |
| `eie_logo.png` | top right | Department of Electrical and Electronic Engineering mark |

## Current status

Neither file is committed to this repository. When a file is absent the header
draws a light placeholder — a hairline box on the card background with the
words `LOGO NOT ADDED` in burgundy — that states the expected path, and the
**Branding assets** row in the health panel reports `DEGRADED`. The dashboard
never crashes and never substitutes an approximate mark.

The placeholder is deliberately **light rather than dark**. A dark filled box
next to the white PolyU header reads as a broken image, which is a worse
impression than an honest "not added yet".

## Aspect ratio

Logos are scaled on **height only**, so width follows and a mark is never
stretched. The header reserves its width from `logo_aspect()`, which measures
the real file when present and falls back to `2.4` for the placeholder. That
means dropping in a real logo cannot reflow the title.

## Adding a logo

1. Save a PNG with a transparent background.
2. Keep the height at or above 84 px so it stays sharp when scaled to 42 px.
3. Place it here using the exact filename from the table.
4. Restart the dashboard. `LogoLabel.refresh()` re-reads the file, so no layout
   change is needed.

## Usage restriction

These marks are the property of their owners. This repository is a student
coursework project.

The header always shows the subtitle *"Student AIoT Mini Project · Department of
Electrical and Electronic Engineering"* directly under the title, so a screenshot
cannot present the window as an official University system. The longer
disclaimer — *"Coursework demonstration build · not an official PolyU product or
service"* — is attached to that subtitle as a tooltip rather than occupying a
third header line, because the header height is part of the 1080p no-scroll
budget.

Both strings live in [`src/dashboard/branding.py`](../../src/dashboard/branding.py)
as `SUBTITLE` and `CONTEXT`. Do not remove either when adding the real marks.
