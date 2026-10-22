def outstanding_total(invoices):
    return sum(i.amount_cents for i in invoices if not i.settled) or 0


def format_reference(number):
    return f"INV-{number}"
