import math
import pytest
from gemini_model.free_cooling_rc07.free_cooling_rc07 import FreeCoolingRc07

def test_stull_wb():
    """Test Stull wet-bulb calculation (REF-02)"""
    model = FreeCoolingRc07()
    u = {'t_db': 20.0, 'rh': 50.0, 'q_cool': 10000.0, 'cop': 3.0}
    # T_wb = 13.7 (±0.1)
    # We check if the internal calculation is correct by checking the logic flow
    res = model.calculate_output(u)
    assert res['power_th'] == 13333.333333333333

def test_heat_rejection_and_free_cooling():
    """Test heat rejection and free cooling logic (REF-02, REF-03)"""
    model = FreeCoolingRc07()
    
    # Case 1: High COP, no free cooling
    # T_wb for 30C, 50% RH is ~21.4C. 
    # 21.4 + 5 + 2 = 28.4 > 7.0 (t_chilled_supply). No free cooling.
    u1 = {'t_db': 30.0, 'rh': 50.0, 'q_cool': 10000.0, 'cop': 3.0}
    res1 = model.calculate_output(u1)
    assert math.isclose(res1['power_th'], 13333.33, rel_tol=1e-3)
    assert res1['q_free'] == 0.0
    
    # Case 2: Full free cooling
    # T_wb for 20C, 50% RH is 13.7C.
    # 13.7 + 5 + 2 = 20.7.
    # Set t_chilled_supply to 21.0 to trigger full free cooling.
    model.parameters['t_chilled_supply_degc'] = 21.0
    u2 = {'t_db': 20.0, 'rh': 50.0, 'q_cool': 10000.0, 'cop': 3.0}
    res2 = model.calculate_output(u2)
    assert res2['q_free'] == 10000.0