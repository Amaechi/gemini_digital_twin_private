"""RC-05 Method A (Guidebook 1989 correlation) validation tests."""

import pytest

from gemini_model.absorption_chiller import ghc1989


@pytest.mark.parametrize("t_hw_degf, expected", [(180.0, 0.256), (230.0, 0.876), (240.0, 0.994)])
def test_hot_water_factor_matches_card(t_hw_degf, expected):
    """F_T(180/230/240 °F) = 0.256/0.876/0.994 ± 0.005 (book text rounds to 0.26/0.88/0.99)."""
    assert ghc1989.hot_water_factor(t_hw_degf) == pytest.approx(expected, abs=0.005)


def test_condition_factor_at_rating_point():
    """F_c(44 °F, 85 °F) = 0.996 ± 0.002."""
    assert ghc1989.condition_factor(44.0, 85.0) == pytest.approx(0.996, abs=0.002)


def test_combined_capacity_fraction_at_rating_point():
    """F(240 °F, 44/85 °F) = 0.990 ± 0.005."""
    assert ghc1989.capacity_fraction(240.0, 44.0, 85.0) == pytest.approx(0.990, abs=0.005)


def test_cop_at_190_degf():
    """COP(190 °F) = 0.607 ± 0.005."""
    assert ghc1989.cop(190.0) == pytest.approx(0.607, abs=0.005)


def test_zero_capacity_temperature():
    """F_T = 0 at 160.5 °F ≈ 71.4 °C (± 0.5 K)."""
    t_zero = ghc1989.zero_capacity_temperature_degf()
    assert t_zero == pytest.approx(160.5, abs=0.9)
    assert ghc1989.degf_to_degc(t_zero) == pytest.approx(71.4, abs=0.5)
    assert ghc1989.hot_water_factor(t_zero) == pytest.approx(0.0, abs=1e-12)


@pytest.mark.parametrize(
    "t_hw_degc, f_t, cop",
    [(85.0, 0.32, 0.60), (90.0, 0.43, 0.61), (100.0, 0.66, 0.63), (105.0, 0.77, 0.65)],
)
def test_si_table_of_the_card(t_hw_degc, f_t, cop):
    """RC-05 SI values: F_T and COP at 85/90/100/105 °C (card rounds to 2 decimals)."""
    t_hw_degf = ghc1989.degc_to_degf(t_hw_degc)
    assert ghc1989.hot_water_factor(t_hw_degf) == pytest.approx(f_t, abs=0.01)
    assert ghc1989.cop(t_hw_degf) == pytest.approx(cop, abs=0.006)


def test_oit_case_is_a_documented_comparison():
    """OIT case (p.263), documented comparison: Method A is below the book's F ≈ 0.48.

    The book selected a nominal 312 ton machine for a 150 ton duty at 190 °F; Method A gives
    F = 0.382 at 44/85 °F (≈ 392 ton) and 0.416 at 48 °F chilled water.
    """
    f_44 = ghc1989.capacity_fraction(190.0, 44.0, 85.0)
    f_48 = ghc1989.capacity_fraction(190.0, 48.0, 85.0)
    assert f_44 == pytest.approx(0.382, abs=0.002)
    assert 150.0 / f_44 == pytest.approx(392.0, abs=2.0)
    assert f_48 == pytest.approx(0.416, abs=0.002)
    book_fraction = 150.0 / 312.0
    assert f_44 < f_48 < book_fraction  # Method A is conservative


def test_unit_conversions_round_trip():
    """°C ↔ °F conversions are exact inverses (44 °F = 6.67 °C, 85 °F = 29.44 °C)."""
    assert ghc1989.degf_to_degc(44.0) == pytest.approx(6.6667, abs=1e-4)
    assert ghc1989.degf_to_degc(85.0) == pytest.approx(29.4444, abs=1e-4)
    assert ghc1989.degc_to_degf(ghc1989.degf_to_degc(123.4)) == pytest.approx(123.4, abs=1e-12)
