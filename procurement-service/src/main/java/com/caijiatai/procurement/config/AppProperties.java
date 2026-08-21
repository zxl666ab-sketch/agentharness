package com.caijiatai.procurement.config;

import java.nio.file.Path;
import java.net.URI;
import java.util.Set;
import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties("app")
public record AppProperties(
        String localOperator,
        Path artifactRoot,
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
        if (agentMode == null || agentMode.isBlank()) {
            agentMode = "kafka";
        }
        if (!Set.of("kafka", "demo").contains(agentMode)) {
            throw new IllegalArgumentException("app.agent-mode must be kafka or demo");
        }
        if (internalHmacKey == null || internalHmacKey.getBytes(java.nio.charset.StandardCharsets.UTF_8).length < 32) {
            throw new IllegalArgumentException("app.internal-hmac-key must be at least 32 bytes");
        }
        if (demoSeed == null) {
            demoSeed = new DemoSeed(false, (String) null);
        }
        if (outbox == null) {
            outbox = new Outbox(100);
        }
    }

    public record Outbox(long pollDelayMs) {
        public Outbox {
            if (pollDelayMs < 100) {
                pollDelayMs = 100;
            }
        }
    }

    /**
     * Keep the configured seed path as text during Spring binding. Spring Boot 4.1
     * otherwise treats a relative {@link Path} placeholder as a resource path and
     * rejects the default before the application context can start.
     */
    public record DemoSeed(boolean enabled, String root) {
        public DemoSeed {
            if (root == null || root.isBlank()) {
                root = Path.of("..", "output", "procurement-scenarios").toString();
            }
        }

        public DemoSeed(boolean enabled, Path root) {
            this(enabled, root == null ? null : root.toString());
        }

        public Path rootPath() {
            return Path.of(root);
        }
    }
}
