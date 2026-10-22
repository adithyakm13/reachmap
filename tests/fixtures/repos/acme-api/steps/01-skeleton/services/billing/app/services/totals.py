from decimal import Decimal


def round_total(amount: Decimal) -> Decimal:
    """Round an invoice total to whole cents."""
    return round(amount, 2)
