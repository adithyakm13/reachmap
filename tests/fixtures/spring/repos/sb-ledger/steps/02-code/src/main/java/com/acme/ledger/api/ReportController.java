package com.acme.ledger.api;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class ReportController {
    @GetMapping("${ledger.reports.base}/daily")
    public String daily() {
        return "daily";
    }
}
