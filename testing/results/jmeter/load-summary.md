Run window: 179.3 s, 11947 samples, mean 66.6 req/s, peak 82 req/s

| Endpoint | Samples | Errors | Error % | Mean ms | Median ms | p90 ms | p95 ms | p99 ms | Max ms | Latency ms | Connect ms | req/s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| GET /api/auth/me | 1099 | 0 | 0.00 | 10.8 | 8 | 17 | 22 | 69 | 165 | 10.8 | 0.03 | 6.1 |
| GET /api/auth/sso/status | 374 | 0 | 0.00 | 2.7 | 2 | 4 | 6 | 15 | 51 | 2.7 | 0.03 | 2.1 |
| GET /api/register/hierarchy | 373 | 0 | 0.00 | 9.0 | 7 | 15 | 21 | 47 | 74 | 9.0 | 0.01 | 2.1 |
| GET /api/student/badges | 1070 | 0 | 0.00 | 10.9 | 8 | 17 | 25 | 49 | 135 | 10.7 | 0.03 | 6.0 |
| GET /api/student/dashboard | 1096 | 0 | 0.00 | 7.0 | 5 | 12 | 18 | 39 | 81 | 7.0 | 0.02 | 6.1 |
| GET /api/student/jobs | 1083 | 0 | 0.00 | 13.2 | 10 | 21 | 28 | 49 | 117 | 13.2 | 0.00 | 6.0 |
| GET /api/student/leaderboards | 1081 | 0 | 0.00 | 7.7 | 6 | 12 | 16 | 37 | 65 | 7.7 | 0.00 | 6.0 |
| GET /api/student/ledger | 1073 | 0 | 0.00 | 7.7 | 6 | 12 | 18 | 35 | 71 | 7.7 | 0.01 | 6.0 |
| GET /api/student/placement-readiness | 1062 | 0 | 0.00 | 11.2 | 8 | 18 | 25 | 48 | 149 | 11.2 | 0.01 | 5.9 |
| GET /api/student/profile | 1054 | 0 | 0.00 | 7.5 | 5 | 12 | 19 | 45 | 77 | 7.4 | 0.01 | 5.9 |
| GET /api/student/programme | 1091 | 0 | 0.00 | 6.3 | 5 | 11 | 16 | 27 | 60 | 6.3 | 0.00 | 6.1 |
| GET /api/student/timesheet | 1066 | 0 | 0.00 | 7.4 | 5 | 12 | 17 | 32 | 89 | 7.4 | 0.08 | 5.9 |
| GET /health | 375 | 0 | 0.00 | 2.4 | 2 | 4 | 6 | 22 | 66 | 2.4 | 0.05 | 2.1 |
| POST /api/auth/login | 50 | 0 | 0.00 | 51.2 | 44 | 77 | 85 | 97 | 101 | 51.1 | 1.68 | 0.3 |
| **TOTAL** | 11947 | 0 | 0.00 | 8.8 | 7 | 15 | 21 | 46 | 165 | 8.7 | 0.03 | 66.6 |
