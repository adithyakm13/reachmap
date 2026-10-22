package com.acme.ledger.api;

import com.acme.ledger.service.InvoiceService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/audit")
public class AuditController {
    private final InvoiceService service;

    public AuditController(InvoiceService service) {
        this.service = service;
    }

    @GetMapping
    public String audit() {
        return service.list();
    }
}
