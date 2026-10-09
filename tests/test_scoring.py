from app.scoring import inspection_priority, score_band


def test_score_bands():
    assert score_band(10) == "Routine"
    assert score_band(45) == "Review recommended"
    assert score_band(70) == "High"
    assert score_band(90) == "Urgent human review"


def test_priority_is_bounded_and_explainable():
    result = inspection_priority(
        0.8,
        0.2,
        new_crack_regions=4,
        current_crack_regions=6,
        previous_crack_regions=2,
        element_type="Structural wall",
        structure_type="Load-bearing masonry",
        age_years=70,
    )
    assert 0 <= result.score <= 100
    assert result.factors
    assert abs(sum(item["effective_weight"] for item in result.factors) - 1.0) < 0.01
    assert {item["name"] for item in result.factors} == {
        "Visible progression", "Damage pattern", "Structural context"
    }


def test_one_isolated_nonstructural_crack_stays_routine():
    result = inspection_priority(
        1.0,
        None,
        new_crack_regions=1,
        current_crack_regions=1,
        previous_crack_regions=0,
        element_type="Partition / plaster wall",
        structure_type="RCC framed structure",
        age_years=80,
    )
    assert result.score <= 30
    assert result.band == "Routine"
    assert "isolated" in result.summary.lower()


def test_single_strong_change_on_column_can_request_review_but_not_high():
    result = inspection_priority(
        0.95,
        None,
        new_crack_regions=1,
        current_crack_regions=1,
        previous_crack_regions=0,
        element_type="Column",
        structure_type="RCC framed structure",
        age_years=70,
    )
    assert 31 <= result.score <= 45
    assert result.band == "Review recommended"


def test_multiple_new_regions_can_raise_priority():
    result = inspection_priority(
        0.75,
        0.35,
        new_crack_regions=5,
        current_crack_regions=7,
        previous_crack_regions=1,
        element_type="Structural wall",
        structure_type="Load-bearing masonry",
        age_years=75,
        previous_engineer_assessment="Monitoring recommended",
    )
    assert result.score > 30
    assert result.band in {"Review recommended", "High", "Urgent human review"}


def test_context_alone_cannot_make_unchanged_building_high():
    result = inspection_priority(
        0.0,
        None,
        new_crack_regions=0,
        current_crack_regions=0,
        previous_crack_regions=0,
        element_type="Column",
        structure_type="RCC framed structure",
        age_years=100,
        repair_history="Major structural repairs",
        previous_engineer_assessment="Repairs recommended",
    )
    assert result.score <= 30
    assert result.band == "Routine"


def test_multiple_new_regions_on_second_visit_are_review_not_automatically_high():
    result = inspection_priority(
        0.95,
        None,
        new_crack_regions=2,
        current_crack_regions=3,
        previous_crack_regions=1,
        element_type="Structural wall",
        structure_type="Load-bearing masonry",
        age_years=78,
    )
    assert result.score <= 60
    assert result.band == "Review recommended"
