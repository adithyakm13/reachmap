from decimal import ROUND_HALF_UP, Decimal


def round_total(amount: Decimal) -> Decimal:
    """Round an invoice total to whole cents."""
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
