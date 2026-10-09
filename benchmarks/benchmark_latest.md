# NIRIKSHAN synthetic control benchmark

> Synthetic control benchmark from one image. It measures pipeline regression/failure modes, not real-facade accuracy or structural safety.

> Single isolated crack-like evidence is intentionally allowed to remain Routine; review escalation is evaluated separately on multiple staged regions.

- Unchanged reliable cases: **12**
- False alarms (>30 priority): **0**
- Synthetic unchanged-pair false-alarm rate: **0.0%**
- Synthetic visible-change detection recall: **100.0%**
- Synthetic multi-region priority recall (>30): **100.0%**

| Case | Label | Align | Crack change | New regions | Score | Band |
|---|---|---:|---:|---:|---:|---|
| identical | unchanged | 1.000 | 0.000 | 0 | 0.0 | Routine |
| jpeg_q90 | unchanged | 0.982 | 0.000 | 0 | 0.0 | Routine |
| jpeg_q75 | unchanged | 0.974 | 0.000 | 0 | 0.0 | Routine |
| jpeg_q60 | unchanged | 0.961 | 0.000 | 0 | — | Recapture required |
| gamma_0_8 | unchanged | 0.996 | 0.000 | 0 | 0.0 | Routine |
| gamma_1_2 | unchanged | 0.992 | 0.000 | 0 | 0.0 | Routine |
| brightness_plus_40 | unchanged | 1.000 | 0.000 | 0 | 0.0 | Routine |
| brightness_minus_40 | unchanged | 1.000 | 0.000 | 0 | 0.0 | Routine |
| mild_noise | unchanged | 0.989 | 0.000 | 0 | 0.0 | Routine |
| shadow_gradient | unchanged | 0.987 | 0.000 | 0 | 0.0 | Routine |
| rotate_1deg_shift | unchanged | 0.970 | 0.000 | 0 | 0.0 | Routine |
| rotate_3deg_shift | unchanged | 0.944 | 0.000 | 0 | 0.0 | Routine |
| mild_perspective | unchanged | 0.982 | 0.000 | 0 | 0.0 | Routine |
| single_staged_mark | single_changed | 0.998 | 0.442 | 1 | 18.4 | Routine |
| multiple_staged_marks_3 | multi_changed | 0.995 | 0.956 | 2 | 60.0 | Review recommended |
| multiple_staged_marks_4 | multi_changed | 0.995 | 1.000 | 3 | 79.3 | High |
