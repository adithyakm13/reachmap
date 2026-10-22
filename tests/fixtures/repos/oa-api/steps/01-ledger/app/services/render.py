"""Statements as documents."""


def statement_pdf(statement_id):
    return b"%PDF-1.4 " + statement_id.encode("ascii")
