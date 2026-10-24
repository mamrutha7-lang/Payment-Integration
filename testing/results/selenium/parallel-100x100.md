# Selenium parallel run: 100 instances, 100 concurrent

- Mode: each starts its journey as soon as its browser is up
- Wall clock: 104.3 s
- Passed: 100/100 (100.0 %)
- Throughput: 0.96 complete journeys / s

| Step (ms) | Mean | Median | p90 | p95 | Max |
|---|---:|---:|---:|---:|---:|
| browser_start | 50284 | 50258 | 90784 | 95386 | 100302 |
| login_page | 2154 | 2129 | 2790 | 2965 | 3303 |
| login_to_home | 2386 | 2358 | 3116 | 3559 | 4063 |
| jobs_page | 1364 | 1364 | 1849 | 1935 | 2195 |
| ledger_page | 1274 | 1258 | 1696 | 1794 | 2031 |
| total | 57461 | 57314 | 97708 | 101706 | 104185 |
