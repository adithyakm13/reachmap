package com.acme.ledger.service;

import org.springframework.stereotype.Service;

@Service
public class PricingImpl implements Pricing {
    @Override
    public int price(String id) {
        return id.length() * 12;
    }

    @Override
    public int tax(String id) {
        return price(id) / 5;
    }

    int rounding() {
        return 5;
    }
}
