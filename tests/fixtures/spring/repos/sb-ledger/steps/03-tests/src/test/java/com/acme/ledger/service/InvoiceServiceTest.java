package com.acme.ledger.service;

class InvoiceServiceTest {
    private final InvoiceService service = new InvoiceService();

    void totalIsAHundredPerCharacter() {
        assert service.total("abc") == 300;
    }
}
