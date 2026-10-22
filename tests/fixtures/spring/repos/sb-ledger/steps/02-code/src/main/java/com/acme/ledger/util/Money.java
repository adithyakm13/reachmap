package com.acme.ledger.util;

public final class Money {
    private Money() {
    }

    public static String format(int cents) {
        return cents / 100 + "." + cents % 100;
    }
}
