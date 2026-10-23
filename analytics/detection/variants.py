"""Alternative EARS C2 baseline for the sensitivity analysis (conflict C1, AN-3). Never reads ground truth.

ears_c2_usable_pandas gives the same score as ears_c2, but the baseline is the `baseline` most recent
USABLE weeks (not under-reporting, not a back-fill week) among the weeks at least guard + 1 weeks before t,
instead of a fixed calendar window. A stretch of masked weeks no longer leaves the baseline too thin to
score: the window reaches further back. With nothing masked it is identical to ears_c2.
Pandas only for now. If the sensitivity analysis selects it, port it to Spark before using it in make_alerts.
"""
import numpy as np

from .common import KEYS, add_artefact_flag_pandas, finalize_pandas


def usable_baseline_z_pandas(df, baseline=7, guard=2, min_n=5, min_std=1.0):
    """df must come from add_artefact_flag_pandas (sorted, with _under and reporting_artefact_flag)."""
    d = df.copy()
    usable = d["cases"].astype(float).where(~d["_under"] & ~d["reporting_artefact_flag"]).to_numpy()
    cases = d["cases"].astype(float).to_numpy()
    z = np.full(len(d), np.nan)
    for _, pos in d.groupby(KEYS, sort=False).indices.items():
        v, c = usable[pos], cases[pos]
        out = np.full(len(pos), np.nan)
        for t in range(len(pos)):
            hist = v[: max(t - guard, 0)]                 # weeks 0 .. t-(guard+1)
            hist = hist[~np.isnan(hist)][-baseline:]      # the most recent usable ones
            if len(hist) >= min_n:
                out[t] = (c[t] - hist.mean()) / max(hist.std(ddof=1), min_std)
        z[pos] = out
    d["z"] = z
    return d


def ears_c2_usable_pandas(df, threshold=3.0, baseline=7, guard=2, min_n=5, min_std=1.0,
                          under_ratio=0.85, run_id="local"):
    d = add_artefact_flag_pandas(df, under_ratio)
    d = usable_baseline_z_pandas(d, baseline, guard, min_n, min_std)
    d["score"] = d["z"]
    return finalize_pandas(d, "ears_c2_usable", threshold, run_id)
