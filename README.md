# BVNK API tests

API tests for the BVNK QA simulator (https://qa-simulator.test.bvnk.com), written with python, pytest, httpx and pydantic.

## Stack

Library versions are the ones locked in `uv.lock`.

| Tool | Version | Used for |
|------|---------|----------|
| Python | >= 3.13 | language |
| [uv](https://docs.astral.sh/uv/) | any recent | dependency management and running the tests |
| pytest | 9.1.1 | test runner: fixtures, markers, parametrize, xfail |
| httpx | 0.28.1 | HTTP client behind `BvnkClient` |
| pydantic | 2.13.5 | response models, validated against the OpenAPI schemas |
| pytest-html | 4.2.0 | self-contained HTML report in `reports/` |
| pytest-xdist | 3.8.0 | runs tests in parallel (`-n auto` is set in `pyproject.toml`) |
| python-dotenv | 1.2.3 | loads settings from `.env` |
| tenacity | 9.1.4 | retry loop behind `BvnkClient.wait_for_settlement()` |

The concurrency test uses the standard library (`threading`, `concurrent.futures`), and amounts are compared as
`decimal.Decimal`.

## Setup

1. Install Python 3.13 or newer and [uv](https://docs.astral.sh/uv/getting-started/installation/).
2. Clone the repository and install the dependencies (uv creates the virtual environment for you):

   ```bash
   git clone https://github.com/sagui14/bvnk.git
   cd bvnk
   uv sync
   ```

3. Optional: copy `.env.example` to `.env` if you want to change a setting (see below). The defaults work without it.

## Running

Run the commands from the repository root (the `bvnk` folder created by `git clone`). uv and pytest read their
settings from `pyproject.toml` there, and the report path is relative to it.

```bash
uv run pytest                    # everything
uv run pytest -m "not slow"      # skip the quote expiry boundary test (~50s)
uv run pytest -m security        # or: negative, concurrency, slow
```

Tests run in parallel by default (`-n auto`, from pytest-xdist). Pass `-n0` to run them in a single process.

An HTML report is written to `reports/report.html` on every run.

Settings (env variable or `.env` file):
- `BASE_URL`: the task PDF gives `http://bvnksimulator.pythonanywhere.com`, but that host returns 404 now, so the tests
  run against `https://qa-simulator.test.bvnk.com` by default. It has the same endpoints.

## Project structure

```
api/
  client.py    BvnkClient: one method per endpoint, checks the status code and validates the response.
               The *_raw methods return the response unchecked for negative tests (request() is the fallback).
  models.py    pydantic models for every schema in /openapi.json (+ ErrorResponse, which the spec is missing)
tests/
  conftest.py                fixtures: new account + client per test, second account for isolation tests
  helpers.py                 FEE_RATE and balances(), a (balance, available) snapshot for plain before/after asserts
  test_conversions.py        the 3 required E2E conversions
  test_concurrency.py        parallel accepts / double spend
  test_security.py           tenant isolation, authentication
  test_quote_lifecycle.py    double accept, expiry boundary, funds check on accept
  test_quote_validation.py   amount boundaries
```

To cover a new endpoint, add a method to `BvnkClient`, a model for its response, and a test file.

## Design patterns

The most common ones used in the framework:

| Pattern | Where | What it does here |
|---------|-------|-------------------|
| API client (service object) | `api/client.py` (`BvnkClient`) | Tests call `create_quote()`, `accept_quote()` etc. instead of building URLs and headers, so endpoint details live in one place. Each typed method is built on a `*_raw()` one: the raw method sends the request, the typed one asserts the success status and parses the body. Negative tests call the raw method to check error codes and to send malformed ids. |
| Schema models (DTOs) | `api/models.py` | One pydantic model per spec schema. `ApiModel` is the shared base that maps camelCase JSON to snake_case fields. |
| Test data builder | `api/client.py` (`quote_request()`, `quote_body()`) | Builds a valid quote request from two wallets and an amount, with defaults for everything else. `quote_body()` returns it as a plain dict, which the amount-boundary cases in `test_quote_validation.py` send through `create_quote_raw()`. |
| Fixtures (dependency injection) | `tests/conftest.py` | `account`, `client`, `wallets` and `second_client` are injected by name. Each test gets a new account, and `yield` closes the client afterwards. |
| Context manager | `api/client.py` (`BvnkClient.__enter__` / `__exit__`) | `with BvnkClient(token) as client:` closes the underlying `httpx.Client` even when a test fails. |
| Data-driven tests | `test_conversions.py`, `test_quote_validation.py`, `test_concurrency.py`, `test_quote_lifecycle.py` | `pytest.mark.parametrize` with named `pytest.param` ids runs one test body over a table of cases. |
| Marker-based test selection | `pyproject.toml` (`markers`, `addopts`) | `slow`, `security`, `negative` and `concurrency` select groups with `-m`. |
| Polling / wait-until | `api/client.py` (`BvnkClient.wait_for_settlement()`) | Settlement is asynchronous, so the client polls the quote until `SUCCESS` or a timeout. |
| State snapshot (before/after) | `wallets` fixture, `balances()` in `tests/helpers.py` | Balances are captured before the action and compared after it, so side effects are checked as well as the response. |

## Tests

3 mandatory + 7 additional tests, picked by risk. Every test checks side effects
(balances, persisted quotes) as well as the response.

| TC | Pri | Test | Type |
|----|-----|------|------|
| 01-03 | P0 | `test_conversion`: 1 ETH -> TRX, 420 TRX -> USDT, 987 TRX -> ETH | Functional E2E |
| 04 | P0 | `test_concurrent_accepts_keep_ledger_consistent`: parallel accepts, ledger == sum of settled quotes | Concurrency |
| 05 | P0 | `test_account_cannot_access_another_accounts_resources` | Security |
| 06 | P0 | `test_quote_cannot_be_accepted_twice` | State machine |
| 07 | P0 | `test_quote_expiry_boundary` (slow): accept at 19s / 21s after create | Boundary / state machine |
| 08 | P1 | `test_amount_boundaries`: 0, negative, below precision, exactly available, available + 1 unit | Boundary |
| 09 | P1 | `test_authentication`: missing/invalid/wrong-scheme token, /echo | Security |
| 10 | P0 | `test_accept_is_rejected_when_funds_are_no_longer_available`: a second quote the wallet can't cover | Negative / state machine |

Responses are validated with pydantic models (`api/models.py`) that follow the spec's required fields, so a missing
field, a wrong type or an unknown status fails the test.

Known bugs are marked `xfail(strict=True)`: the test fails until the bug is fixed, then turns into an XPASS failure
so the marker gets removed. Every xfail is a reproduced server defect, not a flaky test: each one was run with
`--runxfail` and fails at the assertion its reason describes. The `BUG-N` at the start of a reason refers to a
defect log kept outside this repo.

## Notes

- Every test calls `/init` to get a new account, so tests don't affect each other's balances.
- Accept returns right away with status `PROCESSING`. The trade settles about 5s later, so the tests poll the quote
  until `SUCCESS` before checking balances.
- Fee is 0.01% of the source amount, and `amountOut = (amountIn - fee) * price`.
- A quote expires 20 seconds after it is created.
- `test_authentication` (TC-09) can fail now and then with `Internal Server Error` from `GET /health` during a parallel
  run. That is a server issue (BUG-10), not a flaky test, so it is not retried.
