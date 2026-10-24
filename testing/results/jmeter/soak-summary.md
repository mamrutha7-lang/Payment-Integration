Run window: 598.1 s, 13533 samples, mean 22.6 req/s, peak 32 req/s

| Endpoint | Samples | Errors | Error % | Mean ms | Median ms | p90 ms | p95 ms | p99 ms | Max ms | Latency ms | Connect ms | req/s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| GET /api/auth/me | 1167 | 0 | 0.00 | 10.2 | 8 | 14 | 19 | 40 | 61 | 10.2 | 0.03 | 2.0 |
| GET /api/auth/sso/status | 650 | 0 | 0.00 | 2.7 | 2 | 3 | 5 | 21 | 39 | 2.7 | 0.02 | 1.1 |
| GET /api/register/hierarchy | 649 | 0 | 0.00 | 8.3 | 7 | 12 | 17 | 28 | 58 | 8.3 | 0.03 | 1.1 |
| GET /api/student/badges | 1149 | 0 | 0.00 | 10.0 | 8 | 14 | 20 | 33 | 63 | 9.9 | 0.03 | 1.9 |
| GET /api/student/dashboard | 1166 | 0 | 0.00 | 6.1 | 5 | 9 | 12 | 22 | 33 | 6.1 | 0.03 | 1.9 |
| GET /api/student/jobs | 1161 | 0 | 0.00 | 11.9 | 10 | 17 | 21 | 35 | 72 | 11.9 | 0.01 | 1.9 |
| GET /api/student/leaderboards | 1158 | 0 | 0.00 | 7.5 | 6 | 10 | 14 | 30 | 88 | 7.5 | 0.01 | 1.9 |
| GET /api/student/ledger | 1152 | 0 | 0.00 | 7.1 | 6 | 9 | 13 | 22 | 76 | 7.0 | 0.01 | 1.9 |
| GET /api/student/placement-readiness | 1145 | 0 | 0.00 | 10.2 | 9 | 14 | 18 | 40 | 69 | 10.2 | 0.03 | 1.9 |
| GET /api/student/profile | 1142 | 0 | 0.00 | 6.8 | 5 | 9 | 14 | 34 | 53 | 6.8 | 0.07 | 1.9 |
| GET /api/student/programme | 1164 | 0 | 0.00 | 6.1 | 5 | 8 | 12 | 30 | 60 | 6.0 | 0.03 | 1.9 |
| GET /api/student/timesheet | 1148 | 0 | 0.00 | 7.0 | 6 | 9 | 14 | 33 | 55 | 7.0 | 0.03 | 1.9 |
| GET /health | 652 | 0 | 0.00 | 2.4 | 2 | 3 | 5 | 23 | 53 | 2.4 | 0.07 | 1.1 |
| POST /api/auth/login | 30 | 0 | 0.00 | 60.2 | 44 | 120 | 161 | 179 | 180 | 60.2 | 2.27 | 0.1 |
| **TOTAL** | 13533 | 0 | 0.00 | 7.9 | 7 | 12 | 16 | 35 | 180 | 7.8 | 0.03 | 22.6 |
