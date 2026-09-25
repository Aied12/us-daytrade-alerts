"""Unit tests for Jamal strategy levels / chase / sizing (no network)."""

from bot.jamal_strategy import (
    JamalState,
    calc_levels,
    classify_chase,
    size_long_position,
)


def test_example1_levels():
    lv = calc_levels(entry=10.0, stop=9.80, tp1_rr=1.5, tp2_rr=2.5)
    assert abs(lv["risk"] - 0.20) < 1e-9
    assert abs(lv["tp1"] - 10.30) < 1e-9
    assert abs(lv["tp2"] - 10.50) < 1e-9


def test_example2_no_chase():
    assert classify_chase(last=10.20, entry=10.05, max_chase_pct=1.0) is True
    assert classify_chase(last=10.10, entry=10.05, max_chase_pct=1.0) is False


def test_example_sizing_cap():
    s = size_long_position(capital_usd=480, risk_pct=1.0, entry=10.0, stop=9.80)
    # max risk $4.80 / $0.20 = 24 shares; notional 240 < 480
    assert s["shares"] == 24
    assert s["risk_usd"] == 4.8
    assert s["position_usd"] == 240.0
    assert s["allowed"] is True


def test_sizing_never_exceeds_capital():
    s = size_long_position(capital_usd=100, risk_pct=50.0, entry=40.0, stop=39.0)
    assert s["shares"] * 40.0 <= 100.0 + 1e-9
    assert s["shares"] <= s["max_shares_by_capital"]


def test_states_enum_unique():
    vals = [s.value for s in JamalState]
    assert len(vals) == len(set(vals))
    assert "NO_CHASE" in vals
    assert "EXIT_BEFORE_CLOSE" in vals
