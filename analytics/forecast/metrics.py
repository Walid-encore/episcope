"""Forecast scoring (AN-5): MAE, 80% interval coverage, weighted interval score (Bracher et al. 2021)."""
import numpy as np
from scipy.stats import nbinom


def mae(y, pred):
    return float(np.mean(np.abs(np.asarray(y, float) - np.asarray(pred, float))))


def covered(y, lower, upper):
    y = np.asarray(y, float)
    return (y >= np.asarray(lower, float)) & (y <= np.asarray(upper, float))


def interval_score(y, lower, upper, alpha=0.2):
    """IS_alpha = (u - l) + (2/alpha)(l - y)[y < l] + (2/alpha)(y - u)[y > u]."""
    y, l, u = (np.asarray(a, float) for a in (y, lower, upper))
    return (u - l) + (2 / alpha) * np.maximum(l - y, 0) + (2 / alpha) * np.maximum(y - u, 0)


def wis(y, median, lower, upper, alpha=0.2):
    """WIS with the median and one central (1-alpha) interval (K=1):
    WIS = (0.5 |y - m| + (alpha/2) IS_alpha) / (K + 0.5). Lower is better. Per-row values."""
    y, m = np.asarray(y, float), np.asarray(median, float)
    return (0.5 * np.abs(y - m) + (alpha / 2) * interval_score(y, lower, upper, alpha)) / 1.5


def nb_quantiles(mu, alpha, qs=(0.1, 0.5, 0.9)):
    """Quantiles of a negative binomial with mean mu and variance mu + alpha*mu^2."""
    r = 1.0 / alpha
    p = 1.0 / (1.0 + alpha * mu)
    return [float(nbinom.ppf(q, r, p)) for q in qs]
