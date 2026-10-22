"""Handlers of the `statements` tag."""

from app.services.render import statement_pdf


def download_statement(statement_id):
    return statement_pdf(statement_id)
