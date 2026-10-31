"""GeoAgent: agentic design of a deep-geothermal district heating and cooling system.

Heating supply 100 °C, cooling supply 20 °C.

Built on the GEMINI geothermal digital twin for the SPE GeoHackathon 2026.
Design principle (spec §1, §4.1): agents propose, deterministic tools compute and check,
only ``DesignVerifier`` can mark a design ACCEPTED, the human approves.
"""

__version__ = "0.1.0"
