from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PriorityResult:
    score: float
    band: str
    factors: list[dict]
    summary: str = ""


def _clamp01(value: float | int | None) -> float:
    if value is None:
        return 0.0
    return max(0.0, min(1.0, float(value)))


def score_band(score: float) -> str:
    if score <= 30:
        return "Routine"
    if score <= 60:
        return "Review recommended"
    if score <= 80:
        return "High"
    return "Urgent human review"


def _element_sensitivity(element_type: str, structure_type: str) -> float:
    """Prototype inspection-context weight, not a structural-capacity estimate."""

    element = (element_type or "Other").strip()
    structure = (structure_type or "Unknown").strip()

    base = {
        "Partition / plaster wall": 0.08,
        "Building exterior / facade": 0.12,
        "Staircase exterior": 0.18,
        "Other": 0.15,
        "Balcony": 0.55,
        "Slab / Chajja": 0.72,
        "Beam": 0.88,
        "Column": 0.95,
        "Structural wall": 0.72,
    }.get(element, 0.15)

    # Structure type changes which element is most structurally meaningful. It never becomes
    # a claim that one structural system is inherently "weak" or "strong".
    if structure == "Load-bearing masonry" and element == "Structural wall":
        base = max(base, 0.95)
    elif structure == "RCC framed structure" and element in {"Column", "Beam", "Slab / Chajja"}:
        base = min(1.0, base + 0.05)
    elif structure == "Steel structure" and element in {"Column", "Beam"}:
        base = min(1.0, base + 0.04)
    elif structure == "Composite / mixed" and element in {"Column", "Beam", "Structural wall", "Slab / Chajja"}:
        base = min(1.0, base + 0.03)
    return _clamp01(base)


def _assessment_signal(previous_engineer_assessment: str) -> float:
    return {
        "No verified record": 0.00,
        "Routine / no action recorded": 0.05,
        "Monitoring recommended": 0.20,
        "Repairs recommended": 0.55,
        "Urgent inspection previously recommended": 1.00,
    }.get((previous_engineer_assessment or "").strip(), 0.00)


def _repair_signal(repair_history: str) -> float:
    return {
        "None known": 0.00,
        "Minor repairs": 0.10,
        "Major structural repairs": 0.30,
        "Unknown": 0.05,
    }.get((repair_history or "").strip(), 0.05)


def inspection_priority(
    crack_change: float,
    trend_change: float | None = None,
    *,
    new_crack_regions: int = 0,
    current_crack_regions: int = 0,
    previous_crack_regions: int = 0,
    element_type: str = "Other",
    structure_type: str = "Unknown",
    age_years: int | None = None,
    repair_history: str = "Unknown",
    previous_engineer_assessment: str = "No verified record",
) -> PriorityResult:
    """Return a transparent inspection-priority score.

    The score is deliberately an *inspection-ordering heuristic*, not a structural-strength,
    safety or collapse-probability model. It accumulates three types of evidence:

    * 55% visible progression between comparable photographs;
    * 30% damage pattern (multiple new crack-like regions and repeated progression);
    * 15% structural context from verified/entered metadata and the photographed element.

    A single isolated crack-like region is deliberately prevented from producing a High or
    Urgent result. One strong change on a highly relevant structural element can request human
    review, but the prototype requires multiple/longitudinal evidence for higher bands.
    """

    raw_change = _clamp01(crack_change)
    trend = _clamp01(trend_change)
    new_regions = max(0, int(new_crack_regions or 0))
    current_regions = max(0, int(current_crack_regions or 0))
    previous_regions = max(0, int(previous_crack_regions or 0))

    element_signal = _element_sensitivity(element_type, structure_type)
    structurally_relevant_element = element_signal >= 0.70

    # One isolated region is weak evidence. Several independent new regions let the raw
    # progression signal contribute much more strongly.
    if new_regions == 0:
        visual_evidence = raw_change * 0.15
    elif new_regions == 1:
        visual_evidence = raw_change * (0.50 if structurally_relevant_element else 0.30)
    else:
        visual_evidence = raw_change
    visual_evidence = _clamp01(visual_evidence)

    multiple_region_signal = _clamp01((new_regions - 1) / 4.0) if new_regions > 1 else 0.0
    region_growth_signal = _clamp01((current_regions - previous_regions) / max(previous_regions, 1) / 3.0)
    pattern_evidence = _clamp01(
        0.55 * multiple_region_signal
        + 0.25 * region_growth_signal
        + 0.20 * trend
    )

    age_signal = 0.0 if age_years is None else _clamp01((int(age_years) - 30) / 70.0)
    assessment_signal = _assessment_signal(previous_engineer_assessment)
    repair_signal = _repair_signal(repair_history)
    context_base = _clamp01(
        0.55 * element_signal
        + 0.15 * age_signal
        + 0.20 * assessment_signal
        + 0.10 * repair_signal
    )
    # Context cannot make an otherwise unchanged building look urgent; it only modifies
    # priority when there is visible or longitudinal evidence to review.
    evidence_gate = _clamp01(max(raw_change * 0.9, multiple_region_signal, trend))
    context_evidence = _clamp01(context_base * evidence_gate)

    score = (
        55.0 * visual_evidence
        + 30.0 * pattern_evidence
        + 15.0 * context_evidence
    )

    single_isolated = new_regions <= 1 and current_regions <= 1 and trend_change is None
    exceptional_single_structural = (
        single_isolated
        and structurally_relevant_element
        and raw_change >= 0.80
    )

    if single_isolated:
        if exceptional_single_structural:
            # One strong change on a column/beam/slab/structural wall deserves a human look,
            # but it cannot jump straight to High based on one candidate region alone.
            score = min(45.0, max(31.0, score))
        else:
            score = min(30.0, score)

    # A small cluster of new regions on only the second comparable visit is normally a
    # review case, not automatically a High case. High priority requires stronger spatial
    # accumulation (3+ independent new regions) or a repeated positive trend.
    if trend_change is None and new_regions <= 2 and score > 60.0:
        score = 60.0

    # Urgent is deliberately reserved for accumulated evidence or an already verified
    # engineer instruction. Classical CV alone should not escalate a single observation.
    urgent_evidence = (
        new_regions >= 3
        and raw_change >= 0.65
        and trend >= 0.25
        and element_signal >= 0.55
    ) or previous_engineer_assessment == "Urgent inspection previously recommended"
    if score > 80.0 and not urgent_evidence:
        score = 80.0

    score = round(max(0.0, min(100.0, score)), 1)
    band = score_band(score)

    factors = [
        {
            "name": "Visible progression",
            "normalized_value": round(visual_evidence, 3),
            "effective_weight": 0.55,
            "contribution": round(55.0 * visual_evidence, 1),
            "detail": f"{new_regions} new crack-like region(s); raw visible change {round(raw_change * 100)}%",
        },
        {
            "name": "Damage pattern",
            "normalized_value": round(pattern_evidence, 3),
            "effective_weight": 0.30,
            "contribution": round(30.0 * pattern_evidence, 1),
            "detail": "Multiple regions and repeated progression carry more weight than one isolated mark.",
        },
        {
            "name": "Structural context",
            "normalized_value": round(context_evidence, 3),
            "effective_weight": 0.15,
            "contribution": round(15.0 * context_evidence, 1),
            "detail": f"{element_type or 'Other'} in a {structure_type or 'Unknown'} building; context adjusts review order only.",
        },
    ]

    if single_isolated and not exceptional_single_structural:
        summary = "One isolated crack-like region is not enough to escalate the building. Continue monitoring for progression or additional damage evidence."
    elif exceptional_single_structural:
        summary = "A single strong change is located on a structurally relevant element, so human review is recommended; it is not a safety verdict."
    elif new_regions >= 2 and band == "Review recommended":
        summary = "Multiple new crack-like regions are visible. Review is recommended because evidence is accumulating across the photographed area."
    elif band == "High":
        summary = "Several indicators of visible progression are accumulating. This area should move higher in the engineer review queue."
    elif band == "Urgent human review":
        summary = "Multiple longitudinal and structural-context indicators justify urgent human review. NIRIKSHAN is not declaring the building unsafe."
    else:
        summary = "No strong accumulated progression is present. Keep the area in routine monitoring."

    return PriorityResult(score=score, band=band, factors=factors, summary=summary)
