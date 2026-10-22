package com.acme.ledger.service;

import org.springframework.stereotype.Service;

@Service
public class InvoiceService {
    public String list() {
        return "invoices";
    }

    public String find(String id) {
        return "invoice " + id;
    }

    public int total(String id) {
        return id.length() * 120;
    }

    public String save(String body) {
        return "saved " + body;
    }

    public void delete(String id) {
        System.out.println("delete " + id);
    }

    public String lines(String id) {
        return "lines of " + id;
    }
}
