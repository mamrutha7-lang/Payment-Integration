# Selenium parallel run: 50 instances, 50 concurrent

- Mode: all released together after start-up (--barrier)
- Wall clock: 120.0 s
- Passed: 46/50 (92.0 %)
- Throughput: 0.38 complete journeys / s

| Step (ms) | Mean | Median | p90 | p95 | Max |
|---|---:|---:|---:|---:|---:|
| browser_start | 10610 | 9990 | 20770 | 22054 | 23468 |
| login_page | 37898 | 38446 | 40928 | 41425 | 46251 |
| login_to_home | 22908 | 22717 | 28686 | 31561 | 34140 |
| jobs_page | 18557 | 19736 | 22469 | 23594 | 26287 |
| ledger_page | 11236 | 10954 | 14532 | 19429 | 22159 |

Failures:

- 4 x TimeoutException: Message: 
