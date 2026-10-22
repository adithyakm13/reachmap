package com.acme.ledger.service;

import org.springframework.stereotype.Service;

@Service
public class CustomerService {
    public String all() {
        return "customers";
    }

    public String fetch(String id) {
        return "customer #" + id;
    }
}
