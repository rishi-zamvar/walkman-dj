"""Energy target curves T: position[0,1] -> energy_n[0,1]. Pure."""
import math


def _single_peak(x):
    # rise to a peak ~70% through, then cool
    if x <= 0.7:
        return math.sin(min(x / 0.7, 1.0) * math.pi / 2)
    return max(0.0, 1.0 - (x - 0.7) / 0.3 * 0.5)


def _wave(x):
    # two peaks and a valley between: "peaks and valleys"
    return 0.5 + 0.45 * math.sin(2 * math.pi * (x * 2 - 0.25))


def _plateau_drop(x):
    if x < 0.75:
        return 0.6 + 0.4 * min(x / 0.25, 1.0)   # ramp to sustained high
    return max(0.0, 1.0 - (x - 0.75) / 0.25)    # late drop


def _monotonic(x):
    return x


TEMPLATES = {
    "single_peak": _single_peak,
    "wave": _wave,
    "plateau_drop": _plateau_drop,
    "monotonic": _monotonic,
}


def target(name: str, pos: float) -> float:
    """Target energy_n at a normalized position (0=start, 1=end)."""
    y = TEMPLATES[name](max(0.0, min(1.0, pos)))
    return max(0.0, min(1.0, y))


def local_maxima(ys) -> list:
    """Indices of strict interior local maxima (for tests / QA)."""
    return [i for i in range(1, len(ys) - 1) if ys[i] > ys[i - 1] and ys[i] > ys[i + 1]]
