"""Unit tests for per-chapter left-mode auto-detection (spider.py)."""

from .spider import detect_left_mode, resolve_left_geometry


def test_center_wins_for_martial_numbers():
    # martial-evolution-158 live means: mean_left~463, mean_edge~714, cx=400
    pairs = [(463.4, 501.0)] * 34  # mean-preserving synthetic
    assert detect_left_mode(pairs, 400.0) == "center"
    assert detect_left_mode(pairs, 470.0) == "center"
    assert detect_left_mode(pairs, 350.0) == "center"


def test_center_wins_for_contract_numbers():
    # contract-admission-1 live means: mean_left~484.8, mean_edge~677.3
    pairs = [(484.8, 385.0)] * 234
    assert detect_left_mode(pairs, 400.0) == "center"
    assert detect_left_mode(pairs, 350.0) == "center"
    assert detect_left_mode(pairs, 470.0) == "center"


def test_edge_detected_when_appropriate():
    # Synthetic edge-mode chapter: true boxes at [100..500] in 800px page,
    # left=edge, widths ~200 -> left mean ~300, edge-center mean ~400.
    pairs = [(100.0, 200.0), (200.0, 200.0), (300.0, 200.0),
             (400.0, 200.0), (500.0, 200.0)]
    # mean_left=300, mean_edge_center=400, cx=400 -> edge wins
    assert detect_left_mode(pairs, 400.0) == "edge"


def test_resolve_equations():
    cl, lc = resolve_left_geometry(500.0, 200.0, "center")
    assert cl == 400.0 and lc == 500.0
    cl, lc = resolve_left_geometry(500.0, 200.0, "edge")
    assert cl == 500.0 and lc == 600.0
    # clamp
    cl, lc = resolve_left_geometry(50.0, 200.0, "center")
    assert cl == 0 and lc == 50.0


def test_empty_defaults_to_center():
    assert detect_left_mode([], 400.0) == "center"
