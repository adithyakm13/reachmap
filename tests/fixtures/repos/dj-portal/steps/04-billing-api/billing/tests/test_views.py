from django.test import SimpleTestCase

from billing import views


class InvoiceViewTests(SimpleTestCase):
    def test_list(self):
        self.assertEqual(views.invoice_list(None).status_code, 200)
