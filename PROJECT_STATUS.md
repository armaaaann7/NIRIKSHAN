# NIRIKSHAN — Project Status

The current project is a runnable end-to-end prototype for visible building-deterioration monitoring and engineer review prioritisation.

## Implemented

- identifiable building registration with address validation;
- optional browser GPS and nearby-building lookup;
- Mumbai ward and PIN-code fields;
- Structural Context Profile:
  - structure type;
  - number of floors;
  - age;
  - primary material;
  - repair history;
  - previous engineer assessment;
  - optional verified structural/test notes;
- simple site-language inspection areas;
- automatic first-photo reference creation;
- duplicate-photo rejection;
- image-quality checks;
- image alignment and illumination normalisation;
- classical crack-like candidate segmentation;
- independent new-region counting;
- comparison with first and previous accepted photos;
- evidence-accumulation priority scoring:
  - 55% visible progression;
  - 30% damage pattern;
  - 15% structural context;
- single isolated crack-like evidence prevented from producing High/Urgent on its own;
- building-level priority driven by the highest current monitored-area result;
- transparent factor explanations;
- before/after and change-overlay evidence;
- engineer feedback controls;
- inspection timestamps and IDs;
- queue archive/delete action;
- Basic Auth and protected evidence routes;
- synthetic regression benchmark;
- automated test suite.

## Important limitation

The current crack segmenter is still a classical-CV baseline. Structural context improves how evidence is prioritised, but the system does not estimate structural strength or certify safety.

## Next major milestone

Train and validate a real crack-segmentation model on public structural-damage data plus carefully labelled local photographs, then evaluate it against the PRD metrics on held-out real imagery with practising engineer review.
