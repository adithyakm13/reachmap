package com.acme.ledger.api;

import com.acme.ledger.service.InvoiceService;
import com.acme.ledger.service.Pricing;
import org.springframework.web.bind.annotation.*;

import static com.acme.ledger.util.Money.format;

@RestController
@RequestMapping(Paths.INVOICES)
public class InvoiceController {
    private final InvoiceService service;
    private final Pricing pricing;

    public InvoiceController(InvoiceService service, Pricing pricing) {
        this.service = service;
        this.pricing = pricing;
    }

    @GetMapping
    public String list() {
        return service.list();
    }

    @GetMapping("/{id}")
    public String get(@PathVariable String id) {
        return service.find(id).trim();
    }

    @GetMapping("/{id}/total")
    public String total(@PathVariable String id) {
        return format(service.total(id));
    }

    @GetMapping("/{id}/price")
    public int price(@PathVariable String id) {
        return pricing.price(id);
    }

    @PostMapping
    public String create(@RequestBody String body) {
        return service.save(body);
    }

    @DeleteMapping("/{id}")
    public void remove(@PathVariable String id) {
        service.delete(id);
    }

    @RequestMapping(value = "/{id}/lines", method = RequestMethod.GET)
    public String lines(@PathVariable String id) {
        return service.lines(id);
    }
}
