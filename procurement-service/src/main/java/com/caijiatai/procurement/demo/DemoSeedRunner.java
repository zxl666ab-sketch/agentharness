package com.caijiatai.procurement.demo;

import com.caijiatai.procurement.artifact.ArtifactStore;
import com.caijiatai.procurement.config.AppProperties;
import com.caijiatai.procurement.quote.ProcurementQuote;
import com.caijiatai.procurement.quote.ProcurementQuoteRepository;
import com.caijiatai.procurement.report.AuditEvent;
import com.caijiatai.procurement.report.AuditEventRepository;
import com.caijiatai.procurement.task.ProcurementDtos;
import com.caijiatai.procurement.task.ProcurementTaskRepository;
import com.caijiatai.procurement.task.ProcurementTaskService;
import com.caijiatai.procurement.task.TaskStatus;
import java.io.ByteArrayInputStream;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

/**
 * Preseeds the deterministic golden demo scenarios (from
 * scripts/generate_procurement_scenarios.py) into the business database.
 * All seeded data is explicitly marked synthetic and never mixed into
 * production audit semantics.
 */
@Component
@ConditionalOnProperty(prefix = "app.demo-seed", name = "enabled", havingValue = "true")
public class DemoSeedRunner implements ApplicationRunner {
    private static final Logger log = LoggerFactory.getLogger(DemoSeedRunner.class);

    private final AppProperties properties;
    private final ProcurementTaskService tasksService;
    private final ProcurementTaskRepository tasks;
    private final ProcurementQuoteRepository quotes;
    private final ArtifactStore artifacts;
    private final AuditEventRepository audit;
    private final JdbcTemplate jdbc;
    private final ObjectMapper mapper;

    public DemoSeedRunner(
            AppProperties properties,
            ProcurementTaskService tasksService,
            ProcurementTaskRepository tasks,
            ProcurementQuoteRepository quotes,
            ArtifactStore artifacts,
            AuditEventRepository audit,
            JdbcTemplate jdbc,
            ObjectMapper mapper) {
        this.properties = properties;
        this.tasksService = tasksService;
        this.tasks = tasks;
        this.quotes = quotes;
        this.artifacts = artifacts;
        this.audit = audit;
        this.jdbc = jdbc;
        this.mapper = mapper;
    }

    @Override
    @Transactional
    public void run(ApplicationArguments args) throws Exception {
        var root = properties.demoSeed().root().toAbsolutePath().normalize();
        if (!Files.isDirectory(root)) {
            throw new IllegalStateException("演示场景目录不存在：" + root);
        }
        if (alreadySeeded()) {
            log.info("演示数据已预置，跳过（root={}）", root);
            return;
        }
        var scenarios = new ArrayList<Path>();
        try (var stream = Files.list(root)) {
            stream.filter(Files::isDirectory).sorted().forEach(scenarios::add);
        }
        if (scenarios.isEmpty()) {
            throw new IllegalStateException("演示场景目录为空：" + root);
        }
        for (var scenarioDir : scenarios) {
            if (!Files.isRegularFile(scenarioDir.resolve("request.json"))
                    || !Files.isRegularFile(scenarioDir.resolve("quotes.json"))) {
                log.warn("跳过非演示场景目录（缺少 request.json 或 quotes.json）：{}", scenarioDir.getFileName());
                continue;
            }
            seedScenario(scenarioDir);
        }
        log.info("演示数据预置完成：{} 套场景（{}）", scenarios.size(), root);
    }

    private boolean alreadySeeded() {
        Integer count = jdbc.queryForObject(
                "select count(*) from procurement_audit_event where actor = 'demo-seed'", Integer.class);
        return count != null && count > 0;
    }

    private void seedScenario(Path scenarioDir) throws Exception {
        var scenarioName = scenarioDir.getFileName().toString();
        var request = readJson(scenarioDir.resolve("request.json"));
        var requirement = requirement(request);
        var detail = tasksService.createStructured(requirement, "demo:" + scenarioName);
        var taskId = String.valueOf(detail.get("id"));
        var sessionId = sha256hex(scenarioName + ":session");
        var runId = sha256hex(scenarioName + ":run");
        var task = tasks.findById(taskId).orElseThrow();
        task.bindAgent(sessionId, runId);
        tasks.saveAndFlush(task);

        var quotesFile = scenarioDir.resolve("quotes.json");
        if (!Files.isRegularFile(quotesFile)) {
            throw new IllegalStateException("场景缺少 quotes.json：" + scenarioDir);
        }
        var quotesJson = readJson(quotesFile);
        int count = 0;
        for (var raw : list(quotesJson.get("quotes"))) {
            importQuote(taskId, scenarioDir, scenarioName, map(raw));
            count += 1;
        }
        task = tasks.findById(taskId).orElseThrow();
        task.setStatus(TaskStatus.READY);
        tasks.save(task);
        audit.save(AuditEvent.create(
                taskId, null, runId, "demo_seed_created", "demo-seed",
                Map.of("scenario", scenarioName, "synthetic", true, "quote_count", count)));
        log.info("演示场景已预置：{}（{} 份报价）", scenarioName, count);
    }

    private void importQuote(String taskId, Path scenarioDir, String scenarioName, Map<String, Object> quote) throws Exception {
        var filename = text(quote.get("filename"));
        var file = scenarioDir.resolve(filename).toAbsolutePath().normalize();
        var bytes = Files.readAllBytes(file);
        var supplier = text(quote.get("supplier"));
        var isPdf = filename.toLowerCase(java.util.Locale.ROOT).endsWith(".pdf");
        var artifact = artifacts.store(
                "procurement_original",
                taskId,
                filename,
                isPdf
                        ? "application/pdf"
                        : "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                new ByteArrayInputStream(bytes),
                Map.of("synthetic", true, "demo_scenario", scenarioName, "case_id", text(quote.get("id"))));
        var fields = map(quote.get("fields"));
        var extractedFields = new LinkedHashMap<String, Object>();
        fields.forEach((name, value) -> extractedFields.put(
                name, Map.of("value", value, "confidence", 1, "status", "accepted")));
        var extracted = new LinkedHashMap<String, Object>();
        extracted.put("fields", extractedFields);
        extracted.put("review_fields", List.of());
        quotes.save(ProcurementQuote.create(
                taskId,
                artifact.getId(),
                supplier,
                filename,
                isPdf ? "pdf" : "xlsx",
                artifact.getSha256(),
                extracted,
                "ready",
                "demo-seed-v1",
                BigDecimal.ZERO));
    }

    private ProcurementDtos.Requirement requirement(Map<String, Object> request) {
        var specs = map(request.get("specifications"));
        var constraintsJson = map(request.get("constraints"));
        var fx = new LinkedHashMap<String, BigDecimal>();
        map(constraintsJson.get("fx_rates")).forEach((key, value) ->
                fx.put(String.valueOf(key), new BigDecimal(String.valueOf(value))));
        var constraints = new ProcurementDtos.Constraints(
                text(constraintsJson.get("base_currency")),
                fx,
                integer(constraintsJson.get("max_lead_days")),
                Boolean.TRUE.equals(constraintsJson.get("invoice_required")),
                decimalOrNull(constraintsJson.get("size_tolerance_mm")),
                decimalOrNull(constraintsJson.get("thickness_tolerance_um")),
                decimalOrNull(constraintsJson.get("max_landed_unit_cost")),
                textOrNull(constraintsJson.get("destination")),
                textOrNull(constraintsJson.get("required_delivery_date")));
        return new ProcurementDtos.Requirement(
                1,
                text(request.get("title")),
                text(request.getOrDefault("category", "ecommerce_packaging")),
                text(request.get("item_name")),
                new BigDecimal(text(request.get("quantity"))),
                text(request.getOrDefault("unit", "piece")),
                specs,
                constraints);
    }

    private Map<String, Object> readJson(Path file) throws Exception {
        var bytes = Files.readAllBytes(file);
        return mapper.readValue(bytes, Map.class);
    }

    private String sha256hex(String value) {
        try {
            var digest = MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest).substring(0, 32);
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException(error);
        }
    }

    private BigDecimal decimalOrNull(Object value) {
        return value == null || String.valueOf(value).isBlank() ? null : new BigDecimal(String.valueOf(value));
    }

    private String textOrNull(Object value) {
        return value == null || String.valueOf(value).isBlank() ? null : String.valueOf(value).strip();
    }

    private int integer(Object value) {
        return new BigDecimal(String.valueOf(value)).intValueExact();
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> map(Object value) {
        return value instanceof Map<?, ?> raw ? (Map<String, Object>) raw : Map.of();
    }

    @SuppressWarnings("unchecked")
    private List<Object> list(Object value) {
        return value instanceof List<?> raw ? (List<Object>) raw : List.of();
    }

    private String text(Object value) {
        return value == null ? "" : String.valueOf(value).strip();
    }
}
