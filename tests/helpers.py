from decimal import Decimal

import httpx

from api.client import BvnkClient
from api.models import ErrorResponse, HTTPValidationError, Wallet

FEE_RATE = Decimal("0.0001")  # 0.01% service fee, taken from the source amount


def assert_error(response: httpx.Response, status: int, detail: str | None = None) -> ErrorResponse:
    """4xx with a {"detail": "<message>"} body (not in the spec, see README)."""
    assert response.status_code == status, f"expected {status}, got {response.status_code}: {response.text}"
    error = ErrorResponse.model_validate_json(response.content)
    if detail is not None:
        assert error.detail == detail
    return error


def assert_validation_error(response: httpx.Response, field: str) -> HTTPValidationError:
    """422 with the HTTPValidationError body from the spec, pointing at `field`."""
    assert response.status_code == 422, f"expected 422, got {response.status_code}: {response.text}"
    error = HTTPValidationError.model_validate_json(response.content)
    assert any(field in e.loc for e in error.detail), f"no validation error for {field!r}: {error.detail}"
    return error


def assert_balances_unchanged(client: BvnkClient, before: dict[str, Wallet]):
    after = client.get_wallets()
    for code, wallet in before.items():
        assert after[code].balance == wallet.balance, f"{code} balance changed"
        assert after[code].available == wallet.available, f"{code} available changed"
