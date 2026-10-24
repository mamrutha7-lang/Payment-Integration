# REEP — test suites, tools and reports

Everything here tests the running stack from the outside, in addition to the
unit and integration suites that already live in `apps/api-py/tests` and
`apps/web/src/**/*.spec.ts` and the functional Playwright e2e suite at the root
(`tests/`, linked to `test-management/`).

| Folder | What | Tool |
|---|---|---|
| [`docs/`](docs/) | Test Strategy, Test Plan, Test Design and Case Specification, **Test Completion Report**, Incident Reports (ISO/IEC/IEEE 29119-3:2021) | — |
| [`api/`](api/) | Swagger/OpenAPI contract tests, functional and security API tests, Schemathesis property-based run | pytest, requests, jsonschema, openapi-spec-validator, Schemathesis |
| [`jmeter/`](jmeter/) | Load, stress, spike and soak test plan, runner and JTL summariser (milliseconds) | Apache JMeter 5.6.3 |
| [`selenium/`](selenium/) | Single-instance UI suite (Page Object Model) and the 1/100-instance parallel runner; Selenium Grid compose file | Selenium 4 |
| [`playwright/`](playwright/) | Non-functional suite: performance, accessibility (axe), responsive, resilience, security | Playwright + @axe-core/playwright |
| [`tools/`](tools/) | `create_load_users.py`: the 110 load-test student accounts | — |
| [`results/`](results/) | The outputs of the 2026-09-29 cycle that the report cites | — |

**Start with [`docs/04-test-completion-report.md`](docs/04-test-completion-report.md).**

## Prerequisites (once)

```bash
docker compose up -d                              # Postgres (pgvector) on :5433
cd apps/api-py && .venv/bin/python -m alembic upgrade head && .venv/bin/python -m app.seed
.venv/bin/python -m uvicorn app.main:app --port 3300 --workers 2 &
cd ../web && npx ng serve --port 4200 &
cd ../api-py && .venv/bin/python ../../testing/tools/create_load_users.py --count 100   # + 10 reserved

cd ../../testing
python3.14 -m venv .venv && .venv/bin/pip install -r requirements.txt    # pytest, Schemathesis, Selenium …
(cd playwright && npm ci)                                                 # Playwright + axe
# JMeter: download apache-jmeter-5.6.3, put bin/ on PATH (or set JMETER=/path/to/jmeter)
```

> **Run one suite at a time.** REEP keeps **one live session per account**,
> and the suites use disjoint accounts (JMeter and Selenium ×100:
> `loadtest001–100`; API: `101/102`; Selenium: `103`; Playwright NFR: `104`;
> Schemathesis: `105`). Two suites, or a suite and a browser signed in as the
> same account, sign each other out. Measurements also need an idle machine.
> Never point any of this at production: the accounts carry a password
> published here, and `create_load_users.py` refuses `ENV=prod`.

## Running each suite

```bash
cd testing

# Unit + integration (existing), on a database of its own (AGENTS.md: never beside a live API)
(cd ../apps/api-py && DATABASE_URL=postgresql+psycopg://reep:reep_dev_password@localhost:5433/reep_test .venv/bin/python -m pytest)
(cd ../apps/web && npx ng test --watch=false)

# API: Swagger/OpenAPI contract + functional + security
.venv/bin/python -m pytest api --junitxml=results/api/junit.xml --html=results/api/report.html --self-contained-html
api/run_schemathesis.sh                      # property-based, GET only (see the script for why)

# Performance (JMeter, non-GUI)
jmeter/run.sh smoke|load|stress|spike|soak|all     # → results/jmeter/<scenario>/index.html (zipped copies of this cycle: results/jmeter/dashboards/) + <scenario>-summary.md

# Selenium
.venv/bin/python -m pytest selenium/test_single_instance.py --html=results/selenium/report.html --self-contained-html
.venv/bin/python selenium/run_parallel.py --instances 1
.venv/bin/python selenium/run_parallel.py --instances 100          # 100 concurrent browsers
#   on a Grid:  docker compose -f selenium/grid/docker-compose.yml up -d --scale chrome=10
#               SELENIUM_REMOTE_URL=http://localhost:4444 .venv/bin/python selenium/run_parallel.py --instances 100

# Playwright
(cd playwright && npx playwright test)       # non-functional → results/playwright-nfr/html
(cd .. && npm run test:e2e)                  # functional e2e (existing, 256 tests) → manual-test-results.csv
```

Environment knobs: `REEP_API` (default `http://localhost:3300`), `REEP_WEB` /
`REEP_BASE_URL` (default `http://localhost:4200`), `CHROME_BIN`,
`CHROMEDRIVER` (ChromeDriver's major version must match the browser's),
`HEADLESS=0`, `SELENIUM_REMOTE_URL`, and JMeter `-J` properties (`threads`,
`rampup`, `duration`, `think_ms`, `sla_ms`, `host`, `port`, `max_users`).
