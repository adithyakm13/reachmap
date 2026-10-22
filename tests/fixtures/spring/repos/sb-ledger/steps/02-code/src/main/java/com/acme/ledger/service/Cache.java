package com.acme.ledger.service;

import org.springframework.stereotype.Service;

@Service
public class Cache {
    public String get(String key) {
        return "cached " + key;
    }
}
