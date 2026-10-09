# NIRIKSHAN — code-review fixes

This document records fixes made after the first adversarial control review of the MVP.

## Reliability / CV

- Spalling/surface-change candidates no longer contribute to the Inspection Priority score.
- Crack-mask comparison now uses an absolute image-area evidence floor in addition to relative growth.
- Tiny JPEG/resampling components are suppressed before scoring.
- Near-identity homographies skip unnecessary sub-pixel warping.
- Obvious crack-mask explosions are marked segmentation-unstable and quarantined instead of converted to an urgent score.
- Every inspection is compared with both the latest accepted capture and the pinned baseline.
- Low-confidence/unstable captures are stored as `quarantined` and never become the next reference.
- Trend calculation includes the current reliable observation and requires enough history.
- Recapture results use the explicit `Recapture required` band rather than a contradictory capped numerical score.
- Demo seeding reuses the normal analysis pipeline.

## Security / data handling

- HTTP Basic authentication protects the UI, API and evidence files by default.
- Dynamic user-supplied values are HTML-escaped before rendering in the browser.
- Image upload type and size are validated.
- SQLite connections are explicitly closed through a context manager.
- Building age and other request fields use typed validation.

## Validation engineering

- Added `scripts/benchmark_controls.py`.
- Added JSON, CSV and Markdown benchmark outputs.
- Added regression tests for JPEG recompression, heavy-compression quarantine, small viewpoint changes, API auth, request validation and capture quarantine/reference behaviour.

## UI / wording

- Corrected `SEVA FIRST` spelling.
- Removed the hard-coded REG code from the interface because final category registration is not confirmed here.
- Changed “guided capture” wording to make clear that the MVP overlay is post-selection rather than live camera alignment.
- Surface/spalling change is visibly labelled experimental and not scored.
