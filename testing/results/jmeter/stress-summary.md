Run window: 180.2 s, 52417 samples, mean 290.8 req/s, peak 398 req/s

| Endpoint | Samples | Errors | Error % | Mean ms | Median ms | p90 ms | p95 ms | p99 ms | Max ms | Latency ms | Connect ms | req/s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| GET /api/auth/me | 4804 | 4 | 0.08 | 343.1 | 364 | 476 | 514 | 711 | 1060 | 343.0 | 0.00 | 26.7 |
| GET /api/auth/sso/status | 1598 | 0 | 0.00 | 138.4 | 159 | 232 | 251 | 314 | 765 | 138.4 | 0.00 | 8.9 |
| GET /api/register/hierarchy | 1597 | 0 | 0.00 | 239.5 | 269 | 395 | 436 | 583 | 890 | 239.5 | 0.00 | 8.9 |
| GET /api/student/badges | 4734 | 0 | 0.00 | 330.8 | 349 | 462 | 506 | 693 | 991 | 330.4 | 0.01 | 26.3 |
| GET /api/student/dashboard | 4788 | 1 | 0.02 | 285.2 | 300 | 405 | 449 | 653 | 1013 | 285.1 | 0.03 | 26.6 |
| GET /api/student/jobs | 4768 | 7 | 0.15 | 396.4 | 420 | 549 | 596 | 781 | 1134 | 396.3 | 0.00 | 26.5 |
| GET /api/student/leaderboards | 4754 | 0 | 0.00 | 300.3 | 317 | 427 | 464 | 620 | 962 | 300.2 | 0.01 | 26.4 |
| GET /api/student/ledger | 4743 | 0 | 0.00 | 288.2 | 303 | 408 | 451 | 661 | 936 | 288.0 | 0.00 | 26.3 |
| GET /api/student/placement-readiness | 4717 | 10 | 0.21 | 374.0 | 395 | 514 | 570 | 799 | 1134 | 373.9 | 0.01 | 26.2 |
| GET /api/student/profile | 4711 | 0 | 0.00 | 286.5 | 302 | 409 | 447 | 607 | 981 | 286.4 | 0.00 | 26.1 |
| GET /api/student/programme | 4778 | 0 | 0.00 | 269.1 | 282 | 386 | 427 | 651 | 969 | 269.0 | 0.00 | 26.5 |
| GET /api/student/timesheet | 4726 | 1 | 0.02 | 285.4 | 302 | 404 | 443 | 598 | 1013 | 285.3 | 0.01 | 26.2 |
| GET /health | 1599 | 0 | 0.00 | 109.6 | 125 | 187 | 206 | 250 | 700 | 109.6 | 0.02 | 8.9 |
| POST /api/auth/login | 100 | 0 | 0.00 | 359.9 | 381 | 566 | 611 | 901 | 929 | 359.9 | 1.19 | 0.6 |
| **TOTAL** | 52417 | 23 | 0.04 | 301.9 | 316 | 458 | 508 | 682 | 1134 | 301.8 | 0.01 | 290.8 |

Failures:

- 2 x GET /api/student/placement-readiness -> 200 The operation lasted too long: It took 1,102 milliseconds, but should not have lasted long
- 1 x GET /api/student/placement-readiness -> 200 The operation lasted too long: It took 1,008 milliseconds, but should not have lasted long
- 1 x GET /api/student/jobs -> 200 The operation lasted too long: It took 1,061 milliseconds, but should not have lasted long
- 1 x GET /api/student/jobs -> 200 The operation lasted too long: It took 1,027 milliseconds, but should not have lasted long
- 1 x GET /api/student/jobs -> 200 The operation lasted too long: It took 1,038 milliseconds, but should not have lasted long
- 1 x GET /api/student/placement-readiness -> 200 The operation lasted too long: It took 1,005 milliseconds, but should not have lasted long
- 1 x GET /api/auth/me -> 200 The operation lasted too long: It took 1,025 milliseconds, but should not have lasted long
- 1 x GET /api/student/jobs -> 200 The operation lasted too long: It took 1,047 milliseconds, but should not have lasted long
- 1 x GET /api/student/placement-readiness -> 200 The operation lasted too long: It took 1,011 milliseconds, but should not have lasted long
- 1 x GET /api/student/jobs -> 200 The operation lasted too long: It took 1,011 milliseconds, but should not have lasted long
- 1 x GET /api/student/placement-readiness -> 200 The operation lasted too long: It took 1,070 milliseconds, but should not have lasted long
- 1 x GET /api/student/jobs -> 200 The operation lasted too long: It took 1,088 milliseconds, but should not have lasted long
- 1 x GET /api/student/timesheet -> 200 The operation lasted too long: It took 1,013 milliseconds, but should not have lasted long
- 1 x GET /api/student/placement-readiness -> 200 The operation lasted too long: It took 1,013 milliseconds, but should not have lasted long
- 1 x GET /api/student/dashboard -> 200 The operation lasted too long: It took 1,013 milliseconds, but should not have lasted long
