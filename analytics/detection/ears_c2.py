"""EARS C2 detector (Hutwagner et al. 2003) - AN-1.

score(t) = (cases(t) - mean(baseline)) / max(std(baseline), min_std)
baseline = 7 weeks ending 3 weeks before t (a 2-week guard band keeps an outbreak that has just
started from inflating its own baseline). Alert when score >= threshold (default 3).
Weeks with fewer than `min_n` usable baseline weeks get no score.

mask=True  (robust):  under-reporting and artefact weeks are excluded from baselines.
mask=False (naive):   baseline uses every week. Used only to show the artefact false alarm.
"""
from .common import (add_artefact_flag_pandas, add_artefact_flag_spark, baseline_z_pandas,
                     baseline_z_spark, finalize_pandas, finalize_spark)


def ears_c2_pandas(df, threshold=3.0, baseline=7, guard=2, min_n=5, min_std=1.0,
                   under_ratio=0.85, mask=True, run_id="local"):
    d = add_artefact_flag_pandas(df, under_ratio)
    d = baseline_z_pandas(d, baseline, guard, min_n, min_std, mask)
    d["score"] = d["z"]
    return finalize_pandas(d, "ears_c2", threshold, run_id)


def ears_c2_spark(sdf, threshold=3.0, baseline=7, guard=2, min_n=5, min_std=1.0,
                  under_ratio=0.85, mask=True, run_id="local"):
    d = add_artefact_flag_spark(sdf, under_ratio)
    d = baseline_z_spark(d, baseline, guard, min_n, min_std, mask)
    d = d.withColumnRenamed("z", "score")
    return finalize_spark(d, "ears_c2", threshold, run_id)
