"""EpiScope generator, part 1: the statistical model (numpy + pandas only, no Spark).

Everything small and "smart" happens here: facilities, weather-driven expected cases per
district/day/disease, negative-binomial counts, injected outbreaks, the silent-then-backfill
facilities, and the stock/bed simulation. The big row-level expansion is in spark_rows.py.

Deterministic: every random stream comes from numpy SeedSequence(seed).spawn(), so the same
(seed, sf, start, end, reference files) always give the same output.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

GROUPS = ["dengue", "diarrhoeal", "ili", "malaria", "other"]
SHARE = {"other": 0.58, "diarrhoeal": 0.17, "ili": 0.14, "dengue": 0.07, "malaria": 0.04}
DISPERSION = {"dengue": 6.0, "diarrhoeal": 8.0, "ili": 8.0, "malaria": 6.0, "other": 14.0}
ROWS_AT_SF1 = 25_000_000          # encounters at SF=1 over the 2021-2025 reference period
FULL_DAYS = 1826                  # 2021-01-01 .. 2025-12-31
LAG_PAD = 60                      # days of weather before `start`, for the lag windows

MALARIA_ENDEMIC = ["Bandarban", "Rangamati", "Khagrachhari", "Cox's Bazar", "Sylhet",
                   "Moulvibazar", "Sunamganj", "Netrokona", "Sherpur", "Mymensingh", "Habiganj"]
URBAN_DENGUE = {"Dhaka": 2.2, "Chattogram": 1.5, "Narayanganj": 1.4, "Gazipur": 1.3, "Khulna": 1.2}
DENGUE_YEAR = {2021: 0.8, 2022: 1.1, 2023: 1.7, 2024: 1.0, 2025: 1.2}

ITEM_COEF = {  # expected stock use per encounter of each disease group
    "ns1_kits": {"dengue": 0.6},
    "iv_saline_units": {"dengue": 1.12, "diarrhoeal": 0.36, "ili": 0.06, "malaria": 0.25},
    "ors_sachets": {"diarrhoeal": 4.0},
    "antimalarial_courses": {"malaria": 0.9},
}
MIN_EXTRA_CASES = 15              # an injected local outbreak must add at least this many expected cases
ADMIT_P = {"dengue": 0.28, "diarrhoeal": 0.12, "ili": 0.06, "malaria": 0.25, "other": 0.08}


@dataclass(frozen=True)
class Config:
    seed: int = 42
    sf: float = 0.05
    start: dt.date = dt.date(2021, 1, 1)
    end: dt.date = dt.date(2025, 12, 31)
    n_facilities: int = 700

    @property
    def n_days(self) -> int:
        return (self.end - self.start).days + 1


@dataclass
class Model:
    cfg: Config
    dates: pd.DatetimeIndex
    dist: pd.DataFrame
    fac: pd.DataFrame
    mu: np.ndarray            # (T, D, G) expected baseline cases
    counts: np.ndarray        # (T, D, G) realised cases incl. injected outbreaks
    outbreaks: pd.DataFrame   # ground truth (outbreaks + reporting artefacts)
    outbreak_days: pd.DataFrame
    artefacts: list


def _rng(seed: int, k: int) -> np.random.Generator:
    return np.random.default_rng(np.random.SeedSequence(seed).spawn(8)[k])


# --------------------------------------------------------------------------- reference data
def load_reference(ref_dir):
    ref = Path(ref_dir)
    dist = (pd.read_csv(ref / "districts.csv", dtype={"district_code": str})
            .sort_values("district_code").reset_index(drop=True))
    weather = pd.read_csv(ref / "weather.csv", dtype={"district_code": str})
    return dist, weather


def build_weather(w, codes, first, last):
    """Daily precip/temp arrays (T, D) for first..last. Gaps and dates beyond the file are
    filled with the district's month-day climatology."""
    w = w.copy()
    w["date"] = pd.to_datetime(w["date"])
    idx = pd.date_range(first, last, freq="D")
    arrays = {}
    for col in ("precip_mm", "temp_mean_c"):
        p = w.pivot(index="date", columns="district_code", values=col).reindex(columns=list(codes))
        md = p.groupby([p.index.month, p.index.day]).mean()
        full = p.reindex(idx)
        if full.isna().values.any():
            keys = pd.MultiIndex.from_arrays([full.index.month, full.index.day])
            clim = md.reindex(keys)
            clim.index = full.index
            full = full.fillna(clim)
            full = full.fillna(full.mean())
        arrays[col] = full.to_numpy(dtype=float)
    return idx, arrays


def lag_mean(x, lo, hi):
    """For every day t: mean of x over days t-hi .. t-lo (inclusive). Early rows clip to day 0."""
    c = np.vstack([np.zeros((1, x.shape[1])), np.cumsum(x, axis=0)])
    T = x.shape[0]
    t = np.arange(T)
    a = np.clip(t - hi, 0, T - 1)
    b = np.clip(t - lo, 0, T - 1)
    return (c[b + 1] - c[a]) / (b - a + 1)[:, None]


# --------------------------------------------------------------------------- facilities (S5)
def make_facilities(cfg, rng, dist):
    pop = dist["population"].to_numpy(float)
    w = pop ** 0.75
    n_d = np.maximum(5, np.round(cfg.n_facilities * w / w.sum()).astype(int))
    biggest = int(np.argmax(pop))
    rows = []
    for i, d in dist.iterrows():
        n = int(n_d[i])
        n_h = max(1, round(0.14 * n))
        n_u = max(2, round(0.34 * n))
        types = (["hospital"] * n_h + ["upazila_health_complex"] * n_u
                 + ["clinic"] * max(0, n - n_h - n_u))
        name = d["district_name"]
        for k, t in enumerate(types):
            if t == "hospital":
                beds = int(np.clip(rng.lognormal(np.log(150), 0.55), 50, 900))
                if k == 0 and i == biggest:
                    beds = int(beds * 1.6)
                icu = int(beds * rng.uniform(0.03, 0.10))
                label = f"{name} Sadar Hospital" if k == 0 else f"{name} General Hospital {k}"
                base_w = 6.0
            elif t == "upazila_health_complex":
                beds = int(rng.choice([31, 50], p=[0.4, 0.6]))
                icu = int(rng.integers(0, 3))
                label = f"{name} Upazila Health Complex {k - n_h + 1}"
                base_w = 3.0
            else:
                beds = int(rng.poisson(4))
                icu = 0
                label = f"{name} Community Clinic {k - n_h - n_u + 1}"
                base_w = 1.0
            weight = base_w * rng.lognormal(0, 0.6)
            if i == biggest and t == "hospital" and k < 3:
                weight *= 5.0                      # Dhaka mega-hospitals -> Spark skew
            lat = float(d["centroid_lat"] + np.clip(rng.normal(0, 0.10), -0.22, 0.22))
            lon = float(d["centroid_lon"] + np.clip(rng.normal(0, 0.10), -0.22, 0.22))
            rows.append(dict(facility_name=label, facility_type=t, district_code=d["district_code"],
                             latitude=round(lat, 5), longitude=round(lon, 5),
                             beds=beds, icu_beds=min(icu, beds), weight=weight))
    fac = pd.DataFrame(rows)
    fac.insert(0, "facility_id", [f"F{i:05d}" for i in range(1, len(fac) + 1)])
    fac["weight"] = fac["weight"] / fac.groupby("district_code")["weight"].transform("sum")
    assert len(fac) >= 600, f"only {len(fac)} facilities; raise --facilities"
    return fac


# --------------------------------------------------------------------------- expected cases
def expected_cases(cfg, rng, dist, idx, wx):
    ext = LAG_PAD
    rain, temp = wx["precip_mm"], wx["temp_mean_c"]

    def z(a):
        return (a - a.mean()) / (a.std() + 1e-9)

    z_rain_l = z(np.log1p(lag_mean(rain, 28, 56)))[ext:]      # dengue: 4-8 week lag
    z_temp_l = z(lag_mean(temp, 28, 56))[ext:]
    z_rain_m = z(np.log1p(lag_mean(rain, 14, 28)))[ext:]      # malaria: 2-4 week lag
    z_rain_s = z(np.log1p(lag_mean(rain, 7, 14)))[ext:]       # diarrhoeal: 1-2 week lag
    z_temp_s = z(lag_mean(temp, 0, 6))[ext:]

    dates = idx[ext:]
    T, D = len(dates), len(dist)
    doy = dates.dayofyear.to_numpy()[:, None].astype(float)
    wd = dates.weekday.to_numpy()
    dow = np.where(wd == 4, 0.70, np.where(wd == 5, 0.85, 1.0))   # Fri/Sat weekend in Bangladesh
    dow = (dow / dow.mean())[:, None]
    trend = (1 + 0.03 * (dates - dates[0]).days.to_numpy() / 365.25)[:, None]
    common = trend * dow
    pop = dist["population"].to_numpy(float)[None, :]
    names = dist["district_name"].to_numpy()
    het = {g: rng.lognormal(0, 0.15, D)[None, :] for g in GROUPS}
    tau = 2 * np.pi / 365.0

    susc = np.array([URBAN_DENGUE.get(n, 1.0) for n in names])[None, :]
    year_mult = np.array([DENGUE_YEAR.get(y, 1.0) for y in dates.year])[:, None]
    endemic = np.array([30.0 if n in MALARIA_ENDEMIC else 1.0 for n in names])[None, :]

    raw = {
        "dengue": pop * susc * het["dengue"] * np.exp(0.9 * z_rain_l + 0.6 * z_temp_l) * year_mult * common,
        "diarrhoeal": pop * het["diarrhoeal"] * (1 + 0.25 * np.cos(tau * (doy - 170)))
                      * np.exp(0.30 * z_temp_s + 0.20 * z_rain_s) * common,
        "ili": pop * het["ili"] * (1 + 0.55 * np.cos(tau * (doy - 15))) * np.exp(-0.25 * z_temp_s) * common,
        "malaria": pop * endemic * het["malaria"] * (1 + 0.6 * np.cos(tau * (doy - 205)))
                   * np.exp(0.35 * z_rain_m) * common,
        "other": pop * het["other"] * (1 + 0.05 * np.sin(tau * doy)) * common,
    }
    target = ROWS_AT_SF1 * cfg.sf * cfg.n_days / FULL_DAYS
    mu = np.empty((T, D, len(GROUPS)))
    for gi, g in enumerate(GROUPS):
        mu[:, :, gi] = raw[g] * (SHARE[g] * target / raw[g].sum())
    return mu


def sample_counts(rng, mu):
    counts = np.empty(mu.shape, dtype=np.int64)
    for gi, g in enumerate(GROUPS):
        k = DISPERSION[g]
        counts[:, :, gi] = rng.poisson(rng.gamma(k, mu[:, :, gi] / k))   # negative binomial
    return counts


# --------------------------------------------------------------------------- artefacts
def plan_artefacts(cfg, rng, dist, fac, dates):
    """Silent-then-backfill facilities. Records from the silent window all arrive on one day.
    The silence is <= 17 days so that report_date - onset_date stays <= 21 (schema rule)."""
    T = len(dates)
    pop = dist.set_index("district_code")["population"]
    order = list(pop.sort_values(ascending=False).index)
    specs = [("AR1", 17, order[10:28], (0.25, 0.50)), ("AR2", 12, order[28:48], (0.60, 0.90))]
    out = []
    for aid, length, pool, (a, b) in specs:
        code = pool[int(rng.integers(len(pool)))]
        hosp = fac[(fac.district_code == code) & (fac.facility_type == "hospital")]
        fid = hosp.sort_values("weight", ascending=False).iloc[0]["facility_id"]
        lo, hi = int(T * a), int(T * b)
        s = int(rng.integers(lo, max(lo + 1, hi)))
        e = s + length - 1
        assert e + 1 < T
        out.append(dict(artefact_id=aid, facility_id=fid, district_code=code, s=s, e=e,
                        silent_start=dates[s].date().isoformat(),
                        silent_end=dates[e].date().isoformat(),
                        backfill_date=dates[e + 1].date().isoformat()))
    return out


# --------------------------------------------------------------------------- outbreaks
def _sharp_profile(L):
    t = np.arange(L)
    p = 0.4 * (L - 1)
    sig = np.where(t < p, p / 2.15, (L - 1 - p) / 2.15)
    return np.exp(-0.5 * ((t - p) / sig) ** 2), p


def _slow_profile(L):
    t = np.arange(L)
    p = 0.75 * (L - 1)
    return np.where(t <= p, (t / p) ** 1.5, (L - 1 - t) / (L - 1 - p)), p


def place_and_inject(cfg, rng, dist, mu, counts, dates, artefacts):
    """Add >= 30 outbreaks on top of the baseline counts (in place) and return ground truth."""
    T, D, _ = mu.shape
    codes = dist["district_code"].to_numpy()
    names = dist["district_name"].to_numpy()
    pop = dist["population"].to_numpy(float)
    gi = {g: i for i, g in enumerate(GROUPS)}
    lo, hi = 98, T - 28
    assert hi - lo >= 150, "date range too short for outbreak placement"

    busy = {}
    for a in artefacts:
        busy.setdefault((a["district_code"], "*"), []).append((a["s"] - 21, a["e"] + 21))
    art_all = [(a["s"] - 21, a["e"] + 21) for a in artefacts]

    def free(code, group, s, e, pad=14):
        keys = [(code, group), (code, "*")] + ([("*", "ili")] if group == "ili" else [])
        return not any(s - pad <= b and e + pad >= a for k in keys for a, b in busy.get(k, []))

    recs = []
    # --- national respiratory surges (placed first, one in each half of the usable range)
    for n_i in range(2):
        zl, zh = (lo, lo + (hi - lo) // 2) if n_i == 0 else (lo + (hi - lo) // 2, hi)
        for _ in range(300):
            sigma = rng.uniform(18, 26)
            L = int(6 * sigma) + 1
            if zh - L <= zl:
                continue
            s = int(rng.integers(zl, zh - L))
            e = s + L - 1
            if any(s - 14 <= b and e + 14 >= a for a, b in art_all + busy.get(("*", "ili"), [])):
                continue
            m = rng.uniform(2.2, 3.0)
            t = np.arange(L)
            prof = np.exp(-0.5 * ((t - L // 2) / sigma) ** 2)
            f = rng.lognormal(0, 0.25, D)
            lam = mu[s:e + 1, :, gi["ili"]] * (m - 1) * f[None, :] * prof[:, None]
            extra = rng.poisson(lam)
            counts[s:e + 1, :, gi["ili"]] += extra
            busy.setdefault(("*", "ili"), []).append((s, e))
            recs.append(dict(kind="national_surge", group="ili", d=None, s=s, prof=prof,
                             p=L // 2, mult=m, expected=lam.sum(), extra=extra))
            break
        else:
            raise RuntimeError("could not place national surge")

    def pick_district(group):
        w = pop * (np.isin(names, MALARIA_ENDEMIC) if group == "malaria" else 1.0)
        return int(rng.choice(D, p=w / w.sum()))

    plan = ([("local_sharp", g, n) for g, n in
             [("dengue", 9), ("diarrhoeal", 8), ("malaria", 5), ("ili", 6)]]
            + [("local_slow", g, n) for g, n in [("dengue", 2), ("diarrhoeal", 2)]])
    for kind, group, n in plan:
        for _ in range(n):
            for _try in range(1500):
                d = pick_district(group)
                if kind == "local_sharp":
                    L = int(rng.integers(14, 43))
                    m = rng.uniform(2.2, 4.0) if group == "dengue" else rng.uniform(2.5, 6.0)
                else:
                    L = int(rng.integers(70, 121))
                    m = rng.uniform(1.8, 2.6)
                s = int(rng.integers(lo, hi - L))
                e = s + L - 1
                if not free(codes[d], group, s, e):
                    continue
                prof, p = _sharp_profile(L) if kind == "local_sharp" else _slow_profile(L)
                lam = mu[s:e + 1, d, gi[group]] * (m - 1) * prof
                if lam.sum() < MIN_EXTRA_CASES:          # invisible in the data: pick another cell
                    continue
                extra = rng.poisson(lam)
                counts[s:e + 1, d, gi[group]] += extra
                busy.setdefault((codes[d], group), []).append((s, e))
                recs.append(dict(kind=kind, group=group, d=d, s=s, prof=prof, p=p, mult=m,
                                 expected=lam.sum(), extra=extra))
                break
            else:
                raise RuntimeError(f"could not place {kind} {group} outbreak with >= {MIN_EXTRA_CASES} "
                                   "expected extra cases; the scale factor is too small (try --sf 0.02 or more)")

    # nominal window = days where the curve is >= 10% of its peak
    for r in recs:
        act = np.nonzero(r["prof"] >= 0.1)[0]
        r["ns"], r["ne"] = r["s"] + int(act[0]), r["s"] + int(act[-1])
    recs.sort(key=lambda r: (r["ns"], r["kind"]))

    gt, days = [], []
    iso = lambda i: dates[i].date().isoformat()
    for n_i, r in enumerate(recs, 1):
        oid = f"OB{n_i:03d}"
        code = "ALL" if r["d"] is None else codes[r["d"]]
        gt.append(dict(outbreak_id=oid, kind=r["kind"], disease_group=r["group"], district_code=code,
                       facility_id="", start_date=iso(r["ns"]), peak_date=iso(r["s"] + int(round(r["p"]))),
                       end_date=iso(r["ne"]), peak_multiplier=round(r["mult"], 2),
                       expected_extra_cases=round(float(r["expected"]), 1),
                       realised_extra_cases=int(r["extra"].sum()), backfill_date=""))
        ex = r["extra"]
        if r["d"] is None:
            for ti, di in zip(*np.nonzero(ex > 0)):
                days.append((oid, codes[di], iso(r["s"] + ti), int(ex[ti, di])))
        else:
            for ti in np.nonzero(ex > 0)[0]:
                days.append((oid, code, iso(r["s"] + ti), int(ex[ti])))
    for a in artefacts:
        gt.append(dict(outbreak_id=a["artefact_id"], kind="reporting_artefact", disease_group="",
                       district_code=a["district_code"], facility_id=a["facility_id"],
                       start_date=a["silent_start"], peak_date="", end_date=a["silent_end"],
                       peak_multiplier="", expected_extra_cases="", realised_extra_cases="",
                       backfill_date=a["backfill_date"]))
    return pd.DataFrame(gt), pd.DataFrame(days, columns=["outbreak_id", "district_code", "date", "extra_cases"])


# --------------------------------------------------------------------------- truth export
KIND_MAP = {"local_sharp": "local", "local_slow": "slow_growth", "national_surge": "national_surge"}
TRUTH_COLS = ["outbreak_id", "district_code", "disease", "start_date", "end_date",
              "peak_multiplier", "kind"]                      # exactly as docs/schema.md
EXTRA_COLS = ["outbreak_id", "peak_date", "expected_extra_cases", "realised_extra_cases"]
ARTEFACT_COLS = ["artefact_id", "facility_id", "district_code", "silent_start", "silent_end", "backfill_date"]


def export_truth(gt: pd.DataFrame):
    """Split the internal ground truth into (schema file, extras, reporting artefacts)."""
    ob = gt[gt["kind"] != "reporting_artefact"]
    main = pd.DataFrame({
        "outbreak_id": ob["outbreak_id"], "district_code": ob["district_code"],
        "disease": ob["disease_group"], "start_date": ob["start_date"], "end_date": ob["end_date"],
        "peak_multiplier": ob["peak_multiplier"].astype(float), "kind": ob["kind"].map(KIND_MAP)})[TRUTH_COLS]
    extras = ob[EXTRA_COLS].reset_index(drop=True)
    art = (gt[gt["kind"] == "reporting_artefact"]
           .rename(columns={"outbreak_id": "artefact_id", "start_date": "silent_start", "end_date": "silent_end"})
           [ARTEFACT_COLS].reset_index(drop=True))
    return main.reset_index(drop=True), extras, art


# --------------------------------------------------------------------------- top level
def build(cfg: Config, ref_dir) -> Model:
    r_layout, r_het, r_counts, r_out, r_art = (_rng(cfg.seed, k) for k in range(5))
    dist, wdf = load_reference(ref_dir)
    fac = make_facilities(cfg, r_layout, dist)
    first = pd.Timestamp(cfg.start) - pd.Timedelta(days=LAG_PAD)
    idx, wx = build_weather(wdf, dist["district_code"], first, pd.Timestamp(cfg.end))
    mu = expected_cases(cfg, r_het, dist, idx, wx)
    dates = idx[LAG_PAD:]
    counts = sample_counts(r_counts, mu)
    arte = plan_artefacts(cfg, r_art, dist, fac, dates)
    gt, days = place_and_inject(cfg, r_out, dist, mu, counts, dates, arte)
    assert counts.max() < 1_000_000
    return Model(cfg, dates, dist, fac, mu, counts, gt, days, arte)


# --------------------------------------------------------------------------- stock & beds (S6)
def _restock(demand, rng, S, s):
    T, F = demand.shape
    stock = S.astype(np.int64).copy()
    out = np.zeros((T, F), dtype=np.int32)
    arrive = np.full(F, -1)
    qty = np.zeros(F, dtype=np.int64)
    for t in range(T):
        has = arrive == t
        stock[has] += qty[has]
        arrive[has] = -1
        stock = np.maximum(stock - demand[t], 0)
        need = (stock <= s) & (arrive < 0)
        if need.any():
            lead = rng.integers(3, 11, size=F)
            arrive[need] = t + lead[need]
            qty[need] = (S - stock)[need]
        out[t] = stock
    return out


def simulate_stock(m: Model):
    rng = _rng(m.cfg.seed, 5)
    gi = {g: i for i, g in enumerate(GROUPS)}
    code_to_i = {c: i for i, c in enumerate(m.dist["district_code"])}
    d_idx = m.fac["district_code"].map(code_to_i).to_numpy()
    share = m.fac["weight"].to_numpy()[None, :]
    T, F = len(m.dates), len(m.fac)

    def lam(coefs):
        return sum(c * m.counts[:, d_idx, gi[g]] * share for g, c in coefs.items())

    result = {}
    for item, coefs in ITEM_COEF.items():
        demand = rng.poisson(lam(coefs))
        mean_d = demand.mean(axis=0)
        S = np.ceil(mean_d * 28 + 6)
        result[item] = _restock(demand, rng, S, np.ceil(0.35 * S))

    admit = rng.poisson(lam(ADMIT_P))
    c = np.vstack([np.zeros((1, F)), np.cumsum(admit, axis=0)])
    t = np.arange(T)
    load = c[t + 1] - c[np.maximum(t + 1 - 5, 0)]                  # 5-day average stay
    beds = m.fac["beds"].to_numpy()[None, :]
    occ_base = np.where(m.fac["facility_type"].to_numpy() == "hospital", 0.62, 0.48)[None, :]
    occ_base = np.clip(occ_base * rng.uniform(0.7, 1.3, size=(1, F)), 0.2, 0.9)
    occ = np.round(beds * occ_base * rng.uniform(0.9, 1.1, size=(T, F)) + load)
    result["occupied_beds"] = np.minimum(occ, np.ceil(1.15 * beds)).astype(np.int32)
    return result
