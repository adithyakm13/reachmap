from app.services.ledger import all_totals, invoice_total


def test_total_of_one_invoice():
    assert invoice_total("inv-1")["amount"] == 1200


def test_all_totals():
    assert len(all_totals()) >= 2
