import numpy as np, pandas as pd
from analytics.calendar.preparedness import season_window


def test_sharp_peak_inside_long_elevated_season_uses_cap():
    a = np.full(52, 0.5)
    a[24:46] = 1.6          # weeks 25..46 elevated
    a[43] = 2.5             # sharp peak at week 44
    w = season_window(pd.Series(a, index=range(1, 53)))
    assert (w["onset"], w["peak"], w["end"]) == (25, 44, 46)
