package com.acme.ledger.config;

import com.acme.ledger.service.Cache;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class WebConfig {
    @Bean
    public Cache cache() {
        return new Cache();
    }
}
