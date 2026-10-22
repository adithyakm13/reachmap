package com.acme.ledger.util

fun titleCase(text: String): String {
    return text.replaceFirstChar { it.uppercase() }
}
