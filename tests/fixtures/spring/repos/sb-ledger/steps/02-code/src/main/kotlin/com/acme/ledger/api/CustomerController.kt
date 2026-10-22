package com.acme.ledger.api

import com.acme.ledger.service.CustomerService
import com.acme.ledger.util.titleCase
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

@RestController
@RequestMapping("/customers")
class CustomerController(private val service: CustomerService) {

    @GetMapping
    fun all(): String {
        return service.all()
    }

    @GetMapping("/{id}")
    fun one(@PathVariable id: String): String {
        return titleCase(service.fetch(id))
    }
}
