"""classify_glitch on synthetic pulse lists: (level, start, end), level 1
dominant, 0 recessive, times in seconds, trigger at t = 0."""

from canfd_trigger_check import classify_glitch

WINDOW = (807e-9, 913e-9)
T_END = 6e-6
BRS_END = -4e-9  # BRS ends, and ESI starts, just before the trigger
HDR = {"fdf": 1, "brs": 1, "brs_end": BRS_END}


def frame(esi_width):
    """FDF, res, BRS, ESI, then 2.5 us runs to the end of the record."""
    esi_end = BRS_END + esi_width
    return [
        (0, -4e-6, -3e-6),  # FDF
        (1, -3e-6, -866e-9),  # res
        (0, -866e-9, BRS_END),  # BRS
        (1, BRS_END, esi_end),  # ESI
        (0, esi_end, esi_end + 2.5e-6),
        (1, esi_end + 2.5e-6, T_END),  # open-ended, runs off the record
    ]


def test_esi_before_trigger_counts_as_data_rate_bit():
    verdict, width, why = classify_glitch(frame(500e-9), HDR, WINDOW, T_END)
    assert verdict == "brs", why
    assert abs(width - 862e-9) < 1e-12


def test_no_data_rate_bit_is_wrong():
    verdict, _, why = classify_glitch(frame(1e-6), HDR, WINDOW, T_END)
    assert verdict == "wrong", why
    assert "data-rate bits False" in why
