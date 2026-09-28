import pytest

from api.client import BvnkClient, init_account

# All fixtures here are function-scoped on purpose: every test gets its own account.
# - tests assert exact balance changes, so another test's trade (or one still settling) would break them
# - some tests leave the account in a bad state on purpose (BUG-5 concurrency cases, full-balance quotes)
# - `wallets` is the balance snapshot from before the test, so it has to be taken fresh
# - it keeps the suite safe to run in parallel (pytest-xdist)
# /init is cheap. The run time goes on quote expiry and settlement waits, not setup.


@pytest.fixture
def account():
    return init_account()


@pytest.fixture
def client(account):
    with BvnkClient(account.access_token) as client:
        yield client


@pytest.fixture
def wallets(client):
    return client.get_wallets()


@pytest.fixture
def second_client():
    """A second, unrelated account, for tenant isolation tests."""
    with BvnkClient(init_account().access_token) as client:
        yield client
