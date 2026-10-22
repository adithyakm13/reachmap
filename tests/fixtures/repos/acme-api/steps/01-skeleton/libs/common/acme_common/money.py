from decimal import Decimal


def cents_to_decimal(cents: int) -> Decimal:
    return Decimal(cents) / 100


def format_cents(cents: int, currency: str = "EUR") -> str:
    return f"{cents_to_decimal(cents):.2f} {currency}"
