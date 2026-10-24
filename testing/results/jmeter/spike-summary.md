Run window: 89.6 s, 23299 samples, mean 260.2 req/s, peak 304 req/s

| Endpoint | Samples | Errors | Error % | Mean ms | Median ms | p90 ms | p95 ms | p99 ms | Max ms | Latency ms | Connect ms | req/s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| GET /api/auth/me | 2246 | 2 | 0.09 | 106.5 | 86 | 199 | 237 | 691 | 1648 | 106.5 | 0.00 | 25.1 |
| GET /api/auth/sso/status | 411 | 1 | 0.24 | 48.8 | 28 | 102 | 140 | 338 | 1349 | 48.8 | 0.04 | 4.6 |
| GET /api/register/hierarchy | 409 | 1 | 0.24 | 89.6 | 74 | 165 | 192 | 434 | 1671 | 89.6 | 0.00 | 4.6 |
| GET /api/student/badges | 2178 | 0 | 0.00 | 100.6 | 86 | 192 | 230 | 351 | 980 | 100.4 | 0.01 | 24.3 |
| GET /api/student/dashboard | 2239 | 0 | 0.00 | 71.7 | 58 | 137 | 171 | 342 | 832 | 71.7 | 0.00 | 25.0 |
| GET /api/student/jobs | 2210 | 1 | 0.05 | 137.1 | 118 | 257 | 305 | 563 | 1008 | 137.1 | 0.00 | 24.7 |
| GET /api/student/leaderboards | 2197 | 1 | 0.05 | 97.0 | 77 | 183 | 223 | 787 | 1018 | 97.0 | 0.00 | 24.5 |
| GET /api/student/ledger | 2187 | 1 | 0.05 | 81.8 | 67 | 163 | 193 | 291 | 1002 | 81.7 | 0.01 | 24.4 |
| GET /api/student/placement-readiness | 2158 | 0 | 0.00 | 115.3 | 103 | 223 | 259 | 466 | 827 | 115.3 | 0.00 | 24.1 |
| GET /api/student/profile | 2152 | 0 | 0.00 | 80.4 | 65 | 163 | 198 | 357 | 775 | 80.3 | 0.00 | 24.0 |
| GET /api/student/programme | 2229 | 0 | 0.00 | 70.8 | 54 | 143 | 178 | 348 | 956 | 70.8 | 0.00 | 24.9 |
| GET /api/student/timesheet | 2169 | 0 | 0.00 | 80.5 | 67 | 165 | 195 | 359 | 675 | 80.5 | 0.00 | 24.2 |
| GET /health | 414 | 2 | 0.48 | 41.8 | 20 | 71 | 116 | 542 | 1293 | 41.8 | 0.24 | 4.6 |
| POST /api/auth/login | 100 | 0 | 0.00 | 1884.0 | 1860 | 2264 | 2332 | 2375 | 2477 | 1883.9 | 1.93 | 1.1 |
| **TOTAL** | 23299 | 9 | 0.04 | 100.0 | 73 | 188 | 233 | 667 | 2477 | 100.0 | 0.02 | 260.2 |

Failures:

- 1 x GET /api/auth/sso/status -> 200 The operation lasted too long: It took 1,349 milliseconds, but should not have lasted long
- 1 x GET /health -> 200 The operation lasted too long: It took 1,293 milliseconds, but should not have lasted long
- 1 x GET /health -> 200 The operation lasted too long: It took 1,154 milliseconds, but should not have lasted long
- 1 x GET /api/register/hierarchy -> 200 The operation lasted too long: It took 1,671 milliseconds, but should not have lasted long
- 1 x GET /api/auth/me -> 200 The operation lasted too long: It took 1,648 milliseconds, but should not have lasted long
- 1 x GET /api/auth/me -> 200 The operation lasted too long: It took 1,130 milliseconds, but should not have lasted long
- 1 x GET /api/student/leaderboards -> 200 The operation lasted too long: It took 1,018 milliseconds, but should not have lasted long
- 1 x GET /api/student/jobs -> 200 The operation lasted too long: It took 1,008 milliseconds, but should not have lasted long
- 1 x GET /api/student/ledger -> 200 The operation lasted too long: It took 1,002 milliseconds, but should not have lasted long
