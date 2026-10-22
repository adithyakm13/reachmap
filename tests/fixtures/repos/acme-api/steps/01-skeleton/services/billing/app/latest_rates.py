RATES = {"EUR": 1.0, "USD": 1.08}


def latest_rate(currency: str) -> float:
    return RATES[currency]
