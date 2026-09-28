# BVNK API tests

API tests for the BVNK QA simulator (https://qa-simulator.test.bvnk.com), written with pytest, httpx and pydantic.

## Stack

Library versions are the ones locked in `uv.lock`.

| Tool | Version | Used for |
|------|---------|----------|
| Python | >= 3.11 | language |
| [uv](https://docs.astral.sh/uv/) | any recent | dependency management and running the tests |
| pytest | 9.1.1 | test runner: fixtures, markers, parametrize, xfail |
| httpx | 0.28.1 | HTTP client behind `BvnkClient` |
| pydantic | 2.13.5 | response models, validated against the OpenAPI schemas |
| pytest-html | 4.2.0 | self-contained HTML report in `reports/` |
| python-dotenv | 1.2.3 | loads settings from `.env` |

Concurrency and perf tests use the standard library (`threading`, `concurrent.futures`), and amounts are compared as
`decimal.Decimal`.

## Running

```bash
uv sync
uv run pytest                          # everything except the perf smoke test
uv run pytest -m "not slow and not perf"   # skip the quote expiry boundary test (~50s)
uv run pytest -m perf                  # performance smoke test only
uv run pytest -m security              # or: negative, concurrency, slow
```

An HTML report is written to `reports/report.html` on every run. The perf test attaches its latency stats to it.

Settings (env variable or `.env` file):
- `BASE_URL`: the task PDF gives `http://bvnksimulator.pythonanywhere.com`, but that host returns 404 now, so the tests
  run against `https://qa-simulator.test.bvnk.com` by default. It has the same endpoints.
- `PERF_P95_MS`: p95 latency limit for the perf test (default 2000).

## Project structure

```
api/
  client.py    BvnkClient: one method per endpoint, checks the status code and validates the response.
               request() returns the raw response for negative tests.
  models.py    pydantic models for every schema in /openapi.json (+ ErrorResponse, which the spec is missing)
tests/
  conftest.py                fixtures: new account + client per test, second account for isolation tests
  helpers.py                 shared assertions: error bodies, 422 validation errors, balances unchanged
  test_conversions.py        the 3 required E2E conversions
  test_concurrency.py        parallel accepts / double spend
  test_security.py           tenant isolation, authentication
  test_quote_lifecycle.py    double accept, expiry boundary, amountOut mode, settlement and read consistency
  test_quote_validation.py   amount boundaries, invalid payloads
  test_performance.py        perf smoke test (-m perf)
  test_contract_coverage.py  meta check: every endpoint and schema in the spec has a client method / model
```

To cover a new endpoint, add a method to `BvnkClient`, a model for its response, and a test file.
`test_contract_coverage.py` fails until the client method and model exist.

## Design patterns

| Pattern | Where | What it does here |
|---------|-------|-------------------|
| API client (service object) | `api/client.py` (`BvnkClient`) | Tests call `create_quote()`, `accept_quote()` etc. instead of building URLs and headers. Endpoint details live in one place. |
| Typed happy path + raw escape hatch | `api/client.py` (`request()`), used in `test_quote_validation.py`, `test_security.py`, `test_concurrency.py`, `test_performance.py` | Typed methods assert the success status and parse the body. `request()` returns the raw response so negative tests can check error codes. |
| Schema models (DTOs) | `api/models.py` | One pydantic model per spec schema. `ApiModel` is the shared base that maps camelCase JSON to snake_case fields. |
| Test data builder | `api/client.py` (`quote_request()`, `quote_body()`), `api/models.py` (`QuoteRequest.to_body()`) | Builds a valid quote request from two wallets and an amount, with defaults for everything else. |
| Valid-then-mutate (strategy functions) | `test_quote_validation.py` (`both_amounts`, `no_amount`, ...) | Each negative case starts from a valid body and applies one small mutation function, so each case breaks exactly one thing. |
| Fixtures as dependency injection, fresh fixture per test | `tests/conftest.py` | `account`, `client`, `wallets` and `second_client` are injected by name. Each test gets a new account, and `yield` closes the client afterwards. |
| Custom assertions | `tests/helpers.py`, used in `test_quote_lifecycle.py`, `test_quote_validation.py`, `test_security.py` | `assert_error`, `assert_validation_error` and `assert_balances_unchanged` keep the error-body and no-side-effect checks the same everywhere. |
| State snapshot (before/after) | `wallets` fixture in `tests/conftest.py`, `assert_balances_unchanged` in `tests/helpers.py`, balance checks in every test module | Balances are captured before the action and compared after it, so side effects are checked as well as the response. |
| Data-driven tests | `test_conversions.py`, `test_quote_validation.py`, `test_concurrency.py`, `test_quote_lifecycle.py` (expiry boundary) | `pytest.mark.parametrize` with named `pytest.param` ids runs one test body over a table of cases. |
| Polling / wait-until | `api/client.py` (`BvnkClient.wait_for_settlement()`) | Settlement is asynchronous, so the client polls the quote until `SUCCESS` or a timeout. |
| Barrier-synchronised concurrency | `test_concurrency.py` (`accept_in_parallel()`) | A `threading.Barrier` releases all threads together so the accepts really overlap. |
| Baseline comparison | `test_security.py` | Access to another account's resource must return exactly what a non-existent resource returns, so ownership isn't leaked. |
| Registry / contract mapping | `test_contract_coverage.py` (`ENDPOINTS`) | Maps every `(method, path)` in the spec to its client function, then diffs the map against `/openapi.json`. |
| Strict expected failure | `test_concurrency.py`, `test_quote_validation.py` | Known bugs are `xfail(strict=True)`, so a fix shows up as an XPASS failure (see below). |
| External configuration | `api/client.py` (`BASE_URL`), `test_performance.py` (`PERF_P95_MS`) | Environment and thresholds come from env variables or `.env`, not code. |

## Tests

3 mandatory + 10 additional tests, picked by risk. Every test checks side effects
(balances, persisted quotes) as well as the response.

| TC | Pri | Test | Type |
|----|-----|------|------|
| 01-03 | P0 | `test_conversion`: 1 ETH -> TRX, 420 TRX -> USDT, 987 TRX -> ETH | Functional E2E |
| 04 | P0 | `test_concurrent_accepts_keep_ledger_consistent`: parallel accepts, ledger == sum of settled quotes | Concurrency |
| 05 | P0 | `test_account_cannot_access_another_accounts_resources` | Security |
| 06 | P0 | `test_quote_cannot_be_accepted_twice` | State machine |
| 07 | P0 | `test_quote_expiry_boundary` (slow): accept at 19s / 21s after create | Boundary / state machine |
| 08 | P1 | `test_amount_boundaries`: 0, negative, below precision, exactly available, available + 1 unit | Boundary |
| 09 | P1 | `test_quote_by_amount_out`: quote by the amount to receive | Functional E2E |
| 10 | P1 | `test_invalid_quote_payload` | Negative |
| 11 | P1 | `test_authentication`: missing/invalid/wrong-scheme token, /echo | Security |
| 12 | P2 | `test_settlement_lifecycle_and_read_consistency` | Integration |
| 13 | P2 | `test_create_quote_under_parallel_load` (perf) | Performance |

Responses are validated with pydantic models (`api/models.py`) that follow the spec's required fields, so a missing
field, a wrong type or an unknown status fails the test.

Known bugs are marked `xfail(strict=True)`: the test fails until the bug is fixed, then turns into an XPASS failure
so the marker gets removed.

## Notes

- Every test calls `/init` to get a new account, so tests don't affect each other's balances.
- Accept returns right away with status `PROCESSING`. The trade settles about 5s later, so the tests poll the quote
  until `SUCCESS` before checking balances.
- Fee is 0.01% of the source amount, and `amountOut = (amountIn - fee) * price`.
- A quote expires 20 seconds after it is created.

## Bugs found

1. **Critical, BUG-5:** funds are not reserved on accept, and settlements that overlap overwrite each other. Two
   accepted quotes both end in `SUCCESS`, but only one debit and one credit are applied, whether the quotes together
   exceed the balance or not.
2. BUG-1: negative `amountIn` is accepted (201) and returns a quote with negative `amountOut`.
3. BUG-4: `amountIn` below the currency precision is accepted and rounded to a quote of 0.
4. BUG-2: converting a currency to itself (ETH -> ETH) is accepted, with a price that isn't 1 and a fee charged.
5. BUG-6: in amountOut mode `price` is inverted (source per target), and `fee` isn't rounded to the currency precision.
6. BUG-3: `amountIn = 0` returns "One of 'amountIn' or 'amountOut' must be specified but not both", which is misleading.
7. BUG-7: `acceptanceDate` is overwritten with the settlement time.

Spec gaps: 400/401/404/412 responses are not documented, and their `{"detail": "<string>"}` body doesn't match the
spec's `HTTPValidationError`. `/health` is unauthenticated and exposes global request counts.
