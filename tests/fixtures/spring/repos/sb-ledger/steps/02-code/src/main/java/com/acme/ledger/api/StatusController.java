package com.acme.ledger.api;

import com.acme.ledger.service.Cache;
import java.util.HashMap;
import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class StatusController {
    private final Cache cache;
    private final Map<String, String> flags = new HashMap<>();

    public StatusController(Cache cache) {
        this.cache = cache;
    }

    @GetMapping(Paths.STATUS)
    public String status() {
        return cache.get("status");
    }

    @GetMapping(Paths.STATUS + "/flags")
    public String flags() {
        return Helper.describe(flags.get("mode"));
    }
}
