from decimal import Decimal

from api.models import Wallet

FEE_RATE = Decimal("0.0001")  # 0.01% service fee, taken from the source amount


def balances(wallets: dict[str, Wallet]) -> dict[str, tuple[Decimal, Decimal]]:
    """(balance, available) per currency, so a before/after snapshot compares with a plain `assert ... == ...`."""
    return {code: (wallet.balance, wallet.available) for code, wallet in wallets.items()}
