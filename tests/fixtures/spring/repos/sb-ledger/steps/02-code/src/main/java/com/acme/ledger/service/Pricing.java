package com.acme.ledger.service;

public interface Pricing {
    int price(String id);

    int tax(String id);
}
