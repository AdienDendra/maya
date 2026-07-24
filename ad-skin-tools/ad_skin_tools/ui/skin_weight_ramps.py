"""Single source of truth for Skin Weight Visual names, ramps, and tooltips."""

MODE_OFF = "off"
MODE_HEAT = "heat"
MODE_SPECTRUM = "spectrum"
MODE_GRAYSCALE = "grayscale"

RAMP_MODES = (
    MODE_SPECTRUM,
    MODE_HEAT,
    MODE_GRAYSCALE,
)
VALID_MODES = frozenset((MODE_OFF,) + RAMP_MODES)

RAMPS = {
    MODE_HEAT: (
        (0.000, (0.0, 0.0, 0.0)),
        (0.250, (1.0, 0.0, 0.0)),
        (0.500, (1.0, 0.5, 0.0)),
        (0.750, (1.0, 1.0, 0.0)),
        (1.000, (1.0, 1.0, 1.0)),
    ),
    MODE_SPECTRUM: (
        (0.000, (0.0, 0.0, 0.0)),
        (0.167, (0.0, 0.0, 1.0)),
        (0.333, (0.0, 1.0, 0.0)),
        (0.500, (1.0, 1.0, 0.0)),
        (0.667, (1.0, 0.5, 0.0)),
        (0.833, (1.0, 0.0, 0.0)),
        (1.000, (1.0, 1.0, 1.0)),
    ),
    MODE_GRAYSCALE: (
        (0.000, (0.0, 0.0, 0.0)),
        (0.500, (0.5, 0.5, 0.5)),
        (1.000, (1.0, 1.0, 1.0)),
    ),
}

MODE_TOOLTIPS = {
    MODE_OFF: "Off — normal mesh shading",
    MODE_SPECTRUM: "Black / Blue / Green / Yellow / Orange / Red / White",
    MODE_HEAT: "Black / Red / Orange / Yellow / White",
    MODE_GRAYSCALE: "Black / Grey / White",
}

MODE_ORDER = (
    MODE_OFF,
    MODE_SPECTRUM,
    MODE_HEAT,
    MODE_GRAYSCALE,
)


def ramp_for(mode):
    """Return the immutable ramp points for a visual preset."""

    try:
        return RAMPS[mode]
    except KeyError as exc:
        raise ValueError(
            "Unsupported Skin Weight Visual preset: {}".format(mode)
        ) from exc
