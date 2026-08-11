package com.caijiatai.procurement;

import static org.assertj.core.api.Assertions.assertThat;

import com.caijiatai.procurement.agent.AgentCommandRepository;
import com.caijiatai.procurement.artifact.ArtifactStore;
import com.caijiatai.procurement.config.AppProperties;
import com.caijiatai.procurement.demo.DemoSeedRunner;
import com.caijiatai.procurement.quote.ProcurementQuoteRepository;
import com.caijiatai.procurement.report.AuditEventRepository;
import com.caijiatai.procurement.task.ProcurementTaskRepository;
import com.caijiatai.procurement.task.ProcurementTaskService;
import com.caijiatai.procurement.task.TaskStatus;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.mysql.MySQLContainer;
import tools.jackson.databind.ObjectMapper;

@Testcontainers
@SpringBootTest(properties = {
        "app.agent-internal-token=test-internal-token",
        "app.artifact-root=target/test-artifacts-demo",
        "app.outbox.enabled=false"
})
class DemoSeedIntegrationTest {
    @Container
    static final MySQLContainer MYSQL = new MySQLContainer("mysql:8.0")
            .withDatabaseName("caijiatai_test")
            .withUsername("test")
            .withPassword("test");

    @DynamicPropertySource
    static void database(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", MYSQL::getJdbcUrl);
        registry.add("spring.datasource.username", MYSQL::getUsername);
        registry.add("spring.datasource.password", MYSQL::getPassword);
    }

    @Autowired JdbcTemplate jdbc;
    @Autowired ProcurementTaskRepository tasks;
    @Autowired ProcurementQuoteRepository quotes;
    @Autowired AgentCommandRepository commands;
    @Autowired AuditEventRepository audit;
    @Autowired ProcurementTaskService taskService;
    @Autowired ArtifactStore artifactStore;
    @Autowired AppProperties properties;
    @Autowired ObjectMapper mapper;

    @BeforeEach
    void cleanDatabase() {
        var tables = jdbc.queryForList(
                "select table_name from information_schema.tables "
                        + "where table_schema = database() and table_name <> 'flyway_schema_history'",
                String.class);
        if (!tables.isEmpty()) {
            jdbc.execute("set foreign_key_checks = 0");
            for (var table : tables) {
                jdbc.execute("truncate table " + table);
            }
            jdbc.execute("set foreign_key_checks = 1");
        }
    }

    @Test
    void seedsGoldenScenariosAsSyntheticAndIsIdempotent() throws Exception {
        var root = Path.of("target", "test-demo-scenarios").toAbsolutePath().normalize();
        var scenario = root.resolve("01-测试演示");
        Files.createDirectories(scenario);
        Files.writeString(scenario.resolve("request.json"), """
                {
                  "title": "测试演示采购",
                  "category": "ecommerce_packaging",
                  "item_name": "快递袋",
                  "quantity": 10000,
                  "unit": "piece",
                  "specifications": {
                    "width_mm": "250",
                    "length_mm": "350",
                    "thickness_um": "60",
                    "material": "PE",
                    "color": "白色",
                    "print_colors": 1
                  },
                  "constraints": {
                    "base_currency": "CNY",
                    "fx_rates": {"CNY": "1"},
                    "max_lead_days": 15,
                    "invoice_required": true,
                    "size_tolerance_mm": "2",
                    "thickness_tolerance_um": "3",
                    "max_landed_unit_cost": "0.70",
                    "destination": "华东仓"
                  }
                }
                """, StandardCharsets.UTF_8);
        Files.writeString(scenario.resolve("quotes.json"), """
                {
                  "quotes": [
                    {
                      "id": "demo-a",
                      "supplier": "华东优包",
                      "kind": "xlsx",
                      "layout": "xlsx_vertical",
                      "filename": "华东优包-报价.xlsx",
                      "expected_landed_total_base": "5200.00",
                      "note": "",
                      "fields": {
                        "supplier_name": "华东优包",
                        "item_description": "PE mailer 250x350mm 60um",
                        "material": "PE",
                        "color": "white",
                        "print_colors": 1,
                        "currency": "CNY",
                        "unit_price": "520",
                        "price_basis": 1000,
                        "tax_rate": "0.13",
                        "tax_included": true,
                        "shipping_fee": "0",
                        "shipping_included": true,
                        "moq": 5000,
                        "lead_time_days": 7,
                        "supports_invoice": true,
                        "width_mm": "250",
                        "length_mm": "350",
                        "thickness_um": "60",
                        "payment_terms": "月结 30 天",
                        "valid_until": "2099-12-31"
                      }
                    },
                    {
                      "id": "demo-b",
                      "supplier": "沪上包装",
                      "kind": "xlsx",
                      "layout": "xlsx_horizontal",
                      "filename": "沪上包装-报价.xlsx",
                      "expected_landed_total_base": "6024.00",
                      "note": "",
                      "fields": {
                        "supplier_name": "沪上包装",
                        "item_description": "PE mailer 250x350mm 60um",
                        "material": "PE",
                        "color": "白色",
                        "print_colors": 1,
                        "currency": "CNY",
                        "unit_price": "0.48",
                        "price_basis": 1,
                        "tax_rate": "0.13",
                        "tax_included": false,
                        "shipping_fee": "600",
                        "shipping_included": false,
                        "moq": 3000,
                        "lead_time_days": 9,
                        "supports_invoice": true,
                        "width_mm": "250",
                        "length_mm": "350",
                        "thickness_um": "60",
                        "payment_terms": "月结 30 天",
                        "valid_until": "2099-12-31"
                      }
                    }
                  ]
                }
                """, StandardCharsets.UTF_8);
        Files.write(scenario.resolve("华东优包-报价.xlsx"), new byte[] {1, 2, 3});
        Files.write(scenario.resolve("沪上包装-报价.xlsx"), new byte[] {4, 5, 6});

        var demoProperties = new AppProperties(
                properties.localOperator(), properties.artifactRoot(), properties.agentBaseUrl(),
                properties.agentInternalToken(), properties.allowedViteOrigin(), properties.developmentMode(),
                properties.outbox(), properties.agentMode(), new AppProperties.DemoSeed(true, root));
        var runner = new DemoSeedRunner(
                demoProperties, taskService, tasks, quotes, artifactStore, audit, jdbc, mapper);
        runner.run(null);

        assertThat(tasks.count()).isEqualTo(1);
        var task = tasks.findAll().getFirst();
        assertThat(task.getStatus()).isEqualTo(TaskStatus.READY.wireValue());
        assertThat(task.getSessionId()).matches("[0-9a-f]{32}");
        assertThat(task.getAnalysisRunId()).matches("[0-9a-f]{32}");
        assertThat(quotes.count()).isEqualTo(2);
        assertThat(quotes.findAll()).allSatisfy(quote -> {
            assertThat(quote.getStatus()).isEqualTo("ready");
            assertThat(quote.reviewFields()).isEmpty();
            assertThat(quote.getParserVersion()).isEqualTo("demo-seed-v1");
        });
        assertThat(audit.findAll()).anySatisfy(event -> {
            assertThat(event.getActor()).isEqualTo("demo-seed");
            assertThat(event.getEventType()).isEqualTo("demo_seed_created");
            assertThat(event.getPayload().get("synthetic")).isEqualTo(true);
        });
        var artifactRows = jdbc.queryForList(
                "select metadata from business_artifact where kind = 'procurement_original'");
        assertThat(artifactRows).hasSize(2);
        assertThat(artifactRows).allSatisfy(row ->
                assertThat(String.valueOf(row.get("metadata"))).contains("\"synthetic\": true"));

        runner.run(null);
        assertThat(tasks.count()).isEqualTo(1);
        assertThat(quotes.count()).isEqualTo(2);
    }
}
