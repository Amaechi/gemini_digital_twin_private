.. Modified by the GeoAgent team for SPE GeoHackathon 2026, 2026-11-01: added the
   model_cooling page (WP-02).
.. Modified by the GeoAgent team for SPE GeoHackathon 2026, 2026-11-01: added the
   model_heating page (WP-03).

.. _models-section:

Models
==============================================

Models are the computational building blocks used throughout GEMINI (in
modules and applications). A model maps inputs to
outputs and can be **static** (pure input-output) or **dynamic** (internal
state is updated at each execution). This section documents the physics
and engineering models available in the ``gemini_model`` Python package,
grouped by domain.

For the auto-generated API reference (function/class signatures), see
:doc:`index_sourcecode`.

.. toctree::
    :maxdepth: 1
    :caption: Model guides

    model_well_esp
    model_well_hydraulics
    model_fluid_pvt
    model_reservoir
    model_corrosion
    model_erosion
    model_surface_equipment
    model_cooling
    model_heating
