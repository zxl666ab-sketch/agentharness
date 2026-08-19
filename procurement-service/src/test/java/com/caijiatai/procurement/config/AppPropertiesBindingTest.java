package com.caijiatai.procurement.config;

import static org.assertj.core.api.Assertions.assertThat;

import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.context.annotation.Configuration;

class AppPropertiesBindingTest {
    private final ApplicationContextRunner contextRunner = new ApplicationContextRunner()
            .withUserConfiguration(TestConfiguration.class)
            .withPropertyValues(
                    "app.internal-hmac-key=0123456789abcdef0123456789abcdef",
                    "app.demo-seed.enabled=false",
                    "app.demo-seed.root=../output/procurement-scenarios");

    @Test
    void bindsRelativeDemoSeedRootWithoutTreatingItAsAResourcePath() {
        contextRunner.run(context -> {
            assertThat(context).hasNotFailed();
            var properties = context.getBean(AppProperties.class);
            assertThat(properties.demoSeed().rootPath())
                    .isEqualTo(Path.of("..", "output", "procurement-scenarios"));
        });
    }

    @Configuration(proxyBeanMethods = false)
    @EnableConfigurationProperties(AppProperties.class)
    static class TestConfiguration {}
}
