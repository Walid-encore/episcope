# AN-6 sensitivity of national planning demand

Each conversion value is scaled by 0.75 and 1.25 with everything else fixed. Totals are the 4-week national planning demand (mean and q90).

| Resource | Assumption | Factor | Demand | Change % | Demand q90 | Change % q90 |
|---|---|---|---|---|---|---|
| beds | los_days | 0.75 | 93.9 | -25.0 | 154.1 | -25.0 |
| beds | los_days | 1.25 | 156.5 | +25.0 | 256.9 | +25.0 |
| iv_saline_units | saline_units_per_case | 0.75 | 730.0 | -25.0 | 1,268.7 | -25.0 |
| iv_saline_units | saline_units_per_case | 1.25 | 1,216.6 | +25.0 | 2,114.5 | +25.0 |
| ns1_kits | ns1_tests_per_case | 0.75 | 42.4 | -25.0 | 126.7 | -25.0 |
| ns1_kits | ns1_tests_per_case | 1.25 | 70.6 | +25.0 | 211.2 | +25.0 |
| ors_sachets | ors_sachets_per_case | 0.75 | 3,976.6 | -25.0 | 6,480.0 | -25.0 |
| ors_sachets | ors_sachets_per_case | 1.25 | 6,627.7 | +25.0 | 10,800.0 | +25.0 |

Demand is linear in every value except the admission rate, so a 25% change in a coefficient moves the matching resource by 25%. Beds respond only to length of stay; saline only to saline per case.
