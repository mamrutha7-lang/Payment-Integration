Run window: 29.9 s, 17529 samples, mean 586.5 req/s, peak 678 req/s

| Endpoint | Samples | Errors | Error % | Mean ms | Median ms | p90 ms | p95 ms | p99 ms | Max ms | Latency ms | Connect ms | req/s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| GET /api/auth/me | 146 | 0 | 0.00 | 23.2 | 22 | 28 | 36 | 63 | 73 | 23.1 | 0.00 | 4.9 |
| GET /api/auth/sso/status | 5359 | 0 | 0.00 | 5.3 | 5 | 7 | 9 | 19 | 75 | 5.3 | 0.00 | 179.3 |
| GET /api/register/hierarchy | 5358 | 0 | 0.00 | 17.1 | 16 | 22 | 25 | 54 | 351 | 17.1 | 0.00 | 179.3 |
| GET /api/student/badges | 145 | 0 | 0.00 | 20.5 | 20 | 27 | 30 | 37 | 44 | 20.4 | 0.00 | 4.9 |
| GET /api/student/dashboard | 145 | 0 | 0.00 | 14.8 | 14 | 20 | 21 | 28 | 38 | 14.8 | 0.00 | 4.9 |
| GET /api/student/jobs | 145 | 0 | 0.00 | 31.6 | 29 | 40 | 47 | 84 | 97 | 31.6 | 0.00 | 4.9 |
| GET /api/student/leaderboards | 145 | 0 | 0.00 | 18.0 | 16 | 23 | 25 | 62 | 75 | 18.0 | 0.00 | 4.9 |
| GET /api/student/ledger | 145 | 0 | 0.00 | 15.1 | 15 | 19 | 21 | 28 | 30 | 15.1 | 0.00 | 4.9 |
| GET /api/student/placement-readiness | 145 | 0 | 0.00 | 27.8 | 24 | 32 | 37 | 79 | 357 | 27.8 | 0.00 | 4.9 |
| GET /api/student/profile | 145 | 0 | 0.00 | 14.9 | 14 | 19 | 21 | 30 | 51 | 14.9 | 0.00 | 4.9 |
| GET /api/student/programme | 145 | 0 | 0.00 | 14.6 | 13 | 17 | 21 | 67 | 79 | 14.6 | 0.00 | 4.9 |
| GET /api/student/timesheet | 145 | 0 | 0.00 | 14.9 | 14 | 19 | 22 | 33 | 38 | 14.9 | 0.00 | 4.9 |
| GET /health | 5360 | 0 | 0.00 | 4.6 | 4 | 6 | 8 | 20 | 321 | 4.6 | 0.01 | 179.3 |
| POST /api/auth/login | 1 | 0 | 0.00 | 196.0 | 196 | 196 | 196 | 196 | 196 | 196.0 | 2.00 | 0.0 |
| **TOTAL** | 17529 | 0 | 0.00 | 9.9 | 6 | 19 | 23 | 39 | 357 | 9.9 | 0.00 | 586.5 |
