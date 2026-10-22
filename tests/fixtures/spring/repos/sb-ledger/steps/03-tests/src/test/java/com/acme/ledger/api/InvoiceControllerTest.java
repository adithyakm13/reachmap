package com.acme.ledger.api;

import com.acme.ledger.service.InvoiceService;
import com.acme.ledger.service.PricingImpl;

class InvoiceControllerTest {
    private final InvoiceController controller = new InvoiceController(new InvoiceService(), new PricingImpl());

    void listIsNotEmpty() {
        assert !controller.list().isEmpty();
    }
}
