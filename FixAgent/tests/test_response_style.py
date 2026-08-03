from services.response_style import select_style


def test_style_variant_is_stable_within_same_turn() -> None:
    first = select_style("general_ai", "session-a", "turn-1")
    second = select_style("general_ai", "session-a", "turn-1")

    assert first == second
    assert 0.7 <= first.temperature <= 0.9


def test_grounded_style_is_more_conservative_than_general_ai() -> None:
    general = select_style("general_ai", "session-a", "turn-1")
    grounded = select_style("grounded", "session-a", "turn-1")

    assert grounded.temperature < general.temperature
