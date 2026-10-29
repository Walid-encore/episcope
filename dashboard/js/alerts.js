// Shared alert logic for the what-if detection threshold (VIS-5).
// alerts.json keeps only rows with score above half the default threshold, so the threshold can be lowered to
// about half the default. Re-thresholding is done in the browser from the exported scores:
//   - threshold unset (null): use the pipeline's own alert_flag, unchanged
//   - lowered below the default: rows with score in [threshold, default) also count as alerts
//   - raised above the default: only flagged rows with score >= threshold count
// Rows at or above the default keep the pipeline's alert_flag, so its minimum-case rule still applies.
const cache = new Map();

export function baseThreshold(data, disease) {
  if (!cache.has(disease)) {
    const t = (data.alerts?.rows ?? []).filter(a => a.disease === disease && a.threshold != null)
      .map(a => a.threshold).sort((x, y) => x - y);
    cache.set(disease, t.length ? t[Math.floor(t.length / 2)] : null);
  }
  return cache.get(disease);
}

export function effectiveThreshold(data, disease, s) {
  return s.threshold ?? baseThreshold(data, disease);
}

export function isAlert(data, a, s) {
  if (s.threshold == null) return !!a.alert_flag;
  const base = baseThreshold(data, a.disease) ?? s.threshold;
  return a.score >= s.threshold && (!!a.alert_flag || a.score < base);
}
