from acme_common.money import cents_to_decimal, format_cents


def test_format_cents_uses_two_decimals():
    assert format_cents(1250) == "12.50 EUR"


def test_cents_to_decimal():
    assert str(cents_to_decimal(5)) == "0.05"


def test_format_cents_other_currency():
    assert format_cents(100, "USD") == "1.00 USD"
