# Branding assets

The header shows two institution marks, both optional:

| File | Position | Notes |
| --- | --- | --- |
| `polyu_logo.png` | top left | PolyU mark |
| `eie_logo.png` | top right | Department of Electrical and Electronic Engineering mark |

## Current status

Neither file is committed to this repository. When a file is absent the header
draws a plainly bordered placeholder that states the expected path, and the
**Branding assets** row in the health panel reports `DEGRADED`. The dashboard
never crashes and never substitutes an approximate mark.

## Adding a logo

1. Save a PNG with a transparent background.
2. Keep the height at or above 84 px so it stays sharp when scaled to 42 px.
3. Place it here using the exact filename from the table.
4. Restart the dashboard. `LogoLabel.refresh()` re-reads the file, so no layout
   change is needed.

## Usage restriction

These marks are the property of their owners. This repository is a student
coursework project. The header therefore carries the line *"Student AIoT Mini
Project · Department of Electrical and Electronic Engineering"* and *"Coursework
demonstration build · not an official PolyU product or service"* so that a
screenshot can never imply an official University system. Do not remove that
context line when adding the real marks.
