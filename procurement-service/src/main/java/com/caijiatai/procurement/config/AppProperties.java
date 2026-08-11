package com.caijiatai.procurement.config;

import java.net.URI;
import java.nio.file.Path;
import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties("app")
public record AppProperties(
        String localOperator,
        Path artifactRoot,
        URI agentBaseUrl,
        String agentInternalToken,
        URI allowedViteOrigin,
        boolean developmentMode,
        Outbox outbox,
        String agentMode,
        DemoSeed demoSeed,
        String internalHmacKey) {

    public AppProperties {
        if (localOperator == null || localOperator.isBlank()) {
            localOperator = "采购员";
        }
        if (artifactRoot == null) {
            artifactRoot = Path.of("data", "artifacts");
        }
        if (agentInternalToken == null || agentInternalToken.isBlank()) {
            throw new IllegalArgumentException("app.agent-internal-token must not be blank");
        }
        if (agentMode == null || agentMode.isBlank()) {
            agentMode = "http";
        }
        if (internalHmacKey == null || internalHmacKey.isBlank()) {
            internalHmacKey = "development-only-hmac-key-change-me";
        }
        if (demoSeed == null) {
            demoSeed = new DemoSeed(false, Path.of("..", "output", "procurement-scenarios"));
        }
        if (outbox == null) {
            outbox = new Outbox(500);
        }
    }

    public record Outbox(long pollDelayMs) {
        public Outbox {
            if (pollDelayMs < 100) {
                pollDelayMs = 100;
            }
        }
    }

    public record DemoSeed(boolean enabled, Path root) {}
}
