package com.caijiatai.procurement.demo;

import com.caijiatai.procurement.agent.CanonicalJson;
import com.caijiatai.procurement.approval.PendingDecision;
import com.caijiatai.procurement.approval.PendingDecisionRepository;
import com.caijiatai.procurement.approval.ProcurementDecision;
import com.caijiatai.procurement.approval.ProcurementDecisionRepository;
import com.caijiatai.procurement.artifact.ArtifactStore;
import com.caijiatai.procurement.comparison.ComparisonService;
import com.caijiatai.procurement.config.AppProperties;
import com.caijiatai.procurement.invoice.Invoice;
import com.caijiatai.procurement.invoice.InvoiceRepository;
import com.caijiatai.procurement.order.OrderService;
import com.caijiatai.procurement.order.PurchaseOrder;
import com.caijiatai.procurement.quote.ProcurementQuote;
import com.caijiatai.procurement.quote.ProcurementQuoteRepository;
import com.caijiatai.procurement.report.AuditEvent;
import com.caijiatai.procurement.report.AuditEventRepository;
import com.caijiatai.procurement.settlement.PurchaseSettlement;
import com.caijiatai.procurement.settlement.SettlementRepository;
import com.caijiatai.procurement.settlement.SettlementService;
import com.caijiatai.procurement.task.ProcurementDtos;
import com.caijiatai.procurement.task.ProcurementTask;
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
import java.time.Instant;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
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
    private final ComparisonService comparison;
    private final PendingDecisionRepository pendingDecisions;
    private final ProcurementDecisionRepository decisions;
    private final OrderService orderService;
    private final SettlementService settlementService;
    private final SettlementRepository settlements;
    private final InvoiceRepository invoices;
    private final JdbcTemplate jdbc;
    private final ObjectMapper mapper;

    public DemoSeedRunner(
            AppProperties properties,
            ProcurementTaskService tasksService,
            ProcurementTaskRepository tasks,
            ProcurementQuoteRepository quotes,
            ArtifactStore artifacts,
            AuditEventRepository audit,
            ComparisonService comparison,
            PendingDecisionRepository pendingDecisions,
            ProcurementDecisionRepository decisions,
            OrderService orderService,
            SettlementService settlementService,
            SettlementRepository settlements,
            InvoiceRepository invoices,
            JdbcTemplate jdbc,
            ObjectMapper mapper) {
        this.properties = properties;
        this.tasksService = tasksService;
        this.tasks = tasks;
        this.quotes = quotes;
        this.artifacts = artifacts;
        this.audit = audit;
        this.comparison = comparison;
        this.pendingDecisions = pendingDecisions;
        this.decisions = decisions;
        this.orderService = orderService;
        this.settlementService = settlementService;
        this.settlements = settlements;
        this.invoices = invoices;
        this.jdbc = jdbc;
        this.mapper = mapper;
    }

    @Override
    @Transactional
    public void run(ApplicationArguments args) throws Exception {
        var root = properties.demoSeed().rootPath().toAbsolutePath().normalize();
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
        seedClosedLoopHistories();
        log.info("演示数据预置完成：{} 套场景 + 历史业务种子（{}）", scenarios.size(), root);
    }

    /**
     * 历史业务种子：在演示场景基础上额外生成 3 套已走完审批闭环的 synthetic 任务
     * （approved 决策 + 已生成订单工件），供订单页/报表页/供应商档案演示使用。
     * 全部标记 synthetic，用 demo-seed actor 写审计，不混入冻结评测。
     */
    private void seedClosedLoopHistories() throws Exception {
        seedClosedLoopHistory(
                "history-1-courier-mailer",
                "settled",
                "历史补货：快递袋（华东仓，已成交）",
                "ecommerce_packaging",
                "快递袋",
                "15000",
                "piece",
                Map.of(
                        "width_mm", "250",
                        "length_mm", "350",
                        "thickness_um", "60",
                        "material", "PE",
                        "color", "白色",
                        "print_colors", 1),
                Map.of(
                        "base_currency", "CNY",
                        "fx_rates", Map.of("CNY", "1"),
                        "max_lead_days", 15,
                        "invoice_required", true,
                        "size_tolerance_mm", "2",
                        "thickness_tolerance_um", "3",
                        "max_landed_unit_cost", "0.70",
                        "destination", "华东仓"),
                List.of(
                        quoteFields("华东优包", "华东优包-快递袋-报价.xlsx", fieldsOf(
                                "supplier_name", "华东优包",
                                "item_description", "PE 快递袋 250x350mm 60um",
                                "material", "PE", "color", "白色", "print_colors", 1,
                                "currency", "CNY", "unit_price", "0.50", "price_basis", 1,
                                "tax_rate", "0.13", "tax_included", true,
                                "shipping_fee", "0", "shipping_included", true,
                                "moq", 3000, "lead_time_days", 7, "supports_invoice", true,
                                "width_mm", "250", "length_mm", "350", "thickness_um", "60",
                                "payment_terms", "月结 30 天", "valid_until", "2099-12-31")),
                        quoteFields("沪上包装", "沪上包装-快递袋-报价.xlsx", fieldsOf(
                                "supplier_name", "沪上包装",
                                "item_description", "PE 快递袋 250x350mm 60um",
                                "material", "PE", "color", "白色", "print_colors", 1,
                                "currency", "CNY", "unit_price", "0.46", "price_basis", 1,
                                "tax_rate", "0.13", "tax_included", false,
                                "shipping_fee", "900", "shipping_included", false,
                                "moq", 3000, "lead_time_days", 9, "supports_invoice", true,
                                "width_mm", "250", "length_mm", "350", "thickness_um", "60",
                                "payment_terms", "月结 30 天", "valid_until", "2099-12-31")),
                        quoteFields("江南优品", "江南优品-快递袋-报价.xlsx", fieldsOf(
                                "supplier_name", "江南优品",
                                "item_description", "PE 快递袋 250x350mm 60um",
                                "material", "PE", "color", "白色", "print_colors", 1,
                                "currency", "CNY", "unit_price", "0.55", "price_basis", 1,
                                "tax_rate", "0.13", "tax_included", true,
                                "shipping_fee", "0", "shipping_included", true,
                                "moq", 10000, "lead_time_days", 12, "supports_invoice", true,
                                "width_mm", "250", "length_mm", "350", "thickness_um", "60",
                                "payment_terms", "月结 30 天", "valid_until", "2099-12-31"))));
        seedClosedLoopHistory(
                "history-2-bubble-wrap",
                "shipped",
                "历史补货：气泡膜（华南仓，已成交）",
                "ecommerce_packaging",
                "气泡膜",
                "300",
                "piece",
                Map.of(
                        "width_mm", "500",
                        "length_mm", "100000",
                        "thickness_um", "50",
                        "material", "PE",
                        "color", "透明",
                        "print_colors", 1),
                Map.of(
                        "base_currency", "CNY",
                        "fx_rates", Map.of("CNY", "1"),
                        "max_lead_days", 12,
                        "invoice_required", true,
                        "size_tolerance_mm", "5",
                        "thickness_tolerance_um", "5",
                        "max_landed_unit_cost", "45.0",
                        "destination", "华南仓"),
                List.of(
                        quoteFields("华南气泡包装", "华南气泡包装-气泡膜-报价.xlsx", fieldsOf(
                                "supplier_name", "华南气泡包装",
                                "item_description", "PE 气泡膜 500mm×100m 50um",
                                "material", "PE", "color", "透明", "print_colors", 1,
                                "currency", "CNY", "unit_price", "32.00", "price_basis", 1,
                                "tax_rate", "0.13", "tax_included", true,
                                "shipping_fee", "0", "shipping_included", true,
                                "moq", 100, "lead_time_days", 5, "supports_invoice", true,
                                "width_mm", "500", "length_mm", "100000", "thickness_um", "50",
                                "payment_terms", "月结 30 天", "valid_until", "2099-12-31")),
                        quoteFields("华东气泡制品", "华东气泡制品-气泡膜-报价.xlsx", fieldsOf(
                                "supplier_name", "华东气泡制品",
                                "item_description", "PE 气泡膜 500mm×100m 50um",
                                "material", "PE", "color", "透明", "print_colors", 1,
                                "currency", "CNY", "unit_price", "35.00", "price_basis", 1,
                                "tax_rate", "0.13", "tax_included", true,
                                "shipping_fee", "0", "shipping_included", true,
                                "moq", 200, "lead_time_days", 6, "supports_invoice", true,
                                "width_mm", "500", "length_mm", "100000", "thickness_um", "50",
                                "payment_terms", "月结 30 天", "valid_until", "2099-12-31")),
                        quoteFields("北方包装", "北方包装-气泡膜-报价.xlsx", fieldsOf(
                                "supplier_name", "北方包装",
                                "item_description", "PE 气泡膜 500mm×100m 50um",
                                "material", "PE", "color", "透明", "print_colors", 1,
                                "currency", "CNY", "unit_price", "38.00", "price_basis", 1,
                                "tax_rate", "0.13", "tax_included", true,
                                "shipping_fee", "0", "shipping_included", true,
                                "moq", 150, "lead_time_days", 8, "supports_invoice", true,
                                "width_mm", "500", "length_mm", "100000", "thickness_um", "50",
                                "payment_terms", "月结 30 天", "valid_until", "2099-12-31"))));
        seedClosedLoopHistory(
                "history-3-carton-tape",
                "paid",
                "历史补货：封箱胶带（出口仓，已成交）",
                "ecommerce_packaging",
                "封箱胶带",
                "5000",
                "piece",
                Map.of(
                        "width_mm", "50",
                        "length_mm", "100",
                        "thickness_um", "50",
                        "material", "BOPP",
                        "color", "透明",
                        "print_colors", 1),
                Map.of(
                        "base_currency", "CNY",
                        "fx_rates", Map.of("CNY", "1"),
                        "max_lead_days", 12,
                        "invoice_required", true,
                        "size_tolerance_mm", "2",
                        "thickness_tolerance_um", "5",
                        "max_landed_unit_cost", "3.00",
                        "destination", "出口仓"),
                List.of(
                        quoteFields("嘉兴胶粘", "嘉兴胶粘-封箱胶带-报价.xlsx", fieldsOf(
                                "supplier_name", "嘉兴胶粘",
                                "item_description", "BOPP 封箱胶带 50mm×100m 50um",
                                "material", "BOPP", "color", "透明", "print_colors", 1,
                                "currency", "CNY", "unit_price", "2.20", "price_basis", 1,
                                "tax_rate", "0.13", "tax_included", true,
                                "shipping_fee", "0", "shipping_included", true,
                                "moq", 1000, "lead_time_days", 7, "supports_invoice", true,
                                "width_mm", "50", "length_mm", "100", "thickness_um", "50",
                                "payment_terms", "月结 30 天", "valid_until", "2099-12-31")),
                        quoteFields("苏州胶带制品", "苏州胶带制品-封箱胶带-报价.xlsx", fieldsOf(
                                "supplier_name", "苏州胶带制品",
                                "item_description", "BOPP 封箱胶带 50mm×100m 50um",
                                "material", "BOPP", "color", "透明", "print_colors", 1,
                                "currency", "CNY", "unit_price", "2.40", "price_basis", 1,
                                "tax_rate", "0.13", "tax_included", true,
                                "shipping_fee", "0", "shipping_included", true,
                                "moq", 500, "lead_time_days", 5, "supports_invoice", true,
                                "width_mm", "50", "length_mm", "100", "thickness_um", "50",
                                "payment_terms", "月结 30 天", "valid_until", "2099-12-31")),
                        quoteFields("宁波新材料", "宁波新材料-封箱胶带-报价.xlsx", fieldsOf(
                                "supplier_name", "宁波新材料",
                                "item_description", "BOPP 封箱胶带 50mm×100m 50um",
                                "material", "BOPP", "color", "透明", "print_colors", 1,
                                "currency", "CNY", "unit_price", "2.60", "price_basis", 1,
                                "tax_rate", "0.13", "tax_included", true,
                                "shipping_fee", "0", "shipping_included", true,
                                "moq", 2000, "lead_time_days", 10, "supports_invoice", true,
                                "width_mm", "50", "length_mm", "100", "thickness_um", "50",
                                "payment_terms", "月结 30 天", "valid_until", "2099-12-31"))));
        // K5 历史报价 RAG 演示：同物料（快递袋）三次成交，参考区间可计算（≥3 条）
        seedClosedLoopHistory(
                "history-4-mailer-replenish-2",
                "settled",
                "历史补货：快递袋（华东仓，二次补货，已成交）",
                "ecommerce_packaging",
                "快递袋",
                "20000",
                "piece",
                Map.of(
                        "width_mm", "250",
                        "length_mm", "350",
                        "thickness_um", "60",
                        "material", "PE",
                        "color", "白色",
                        "print_colors", 1),
                Map.of(
                        "base_currency", "CNY",
                        "fx_rates", Map.of("CNY", "1"),
                        "max_lead_days", 15,
                        "invoice_required", true,
                        "size_tolerance_mm", "2",
                        "thickness_tolerance_um", "3",
                        "max_landed_unit_cost", "0.70",
                        "destination", "华东仓"),
                List.of(
                        quoteFields("沪上包装", "沪上包装-快递袋二补-报价.xlsx", fieldsOf(
                                "supplier_name", "沪上包装",
                                "item_description", "PE 快递袋 250x350mm 60um",
                                "material", "PE", "color", "白色", "print_colors", 1,
                                "currency", "CNY", "unit_price", "0.47", "price_basis", 1,
                                "tax_rate", "0.13", "tax_included", true,
                                "shipping_fee", "0", "shipping_included", true,
                                "moq", 3000, "lead_time_days", 8, "supports_invoice", true,
                                "width_mm", "250", "length_mm", "350", "thickness_um", "60",
                                "payment_terms", "月结 30 天", "valid_until", "2099-12-31")),
                        quoteFields("华东优包", "华东优包-快递袋二补-报价.xlsx", fieldsOf(
                                "supplier_name", "华东优包",
                                "item_description", "PE 快递袋 250x350mm 60um",
                                "material", "PE", "color", "白色", "print_colors", 1,
                                "currency", "CNY", "unit_price", "0.52", "price_basis", 1,
                                "tax_rate", "0.13", "tax_included", true,
                                "shipping_fee", "0", "shipping_included", true,
                                "moq", 3000, "lead_time_days", 7, "supports_invoice", true,
                                "width_mm", "250", "length_mm", "350", "thickness_um", "60",
                                "payment_terms", "月结 30 天", "valid_until", "2099-12-31")),
                        quoteFields("江南优品", "江南优品-快递袋二补-报价.xlsx", fieldsOf(
                                "supplier_name", "江南优品",
                                "item_description", "PE 快递袋 250x350mm 60um",
                                "material", "PE", "color", "白色", "print_colors", 1,
                                "currency", "CNY", "unit_price", "0.58", "price_basis", 1,
                                "tax_rate", "0.13", "tax_included", true,
                                "shipping_fee", "0", "shipping_included", true,
                                "moq", 8000, "lead_time_days", 12, "supports_invoice", true,
                                "width_mm", "250", "length_mm", "350", "thickness_um", "60",
                                "payment_terms", "月结 30 天", "valid_until", "2099-12-31"))));
        seedClosedLoopHistory(
                "history-5-mailer-replenish-3",
                "paid",
                "历史补货：快递袋（华东仓，三次补货，已成交）",
                "ecommerce_packaging",
                "快递袋",
                "18000",
                "piece",
                Map.of(
                        "width_mm", "250",
                        "length_mm", "350",
                        "thickness_um", "60",
                        "material", "PE",
                        "color", "白色",
                        "print_colors", 1),
                Map.of(
                        "base_currency", "CNY",
                        "fx_rates", Map.of("CNY", "1"),
                        "max_lead_days", 15,
                        "invoice_required", true,
                        "size_tolerance_mm", "2",
                        "thickness_tolerance_um", "3",
                        "max_landed_unit_cost", "0.70",
                        "destination", "华东仓"),
                List.of(
                        quoteFields("江南优品", "江南优品-快递袋三补-报价.xlsx", fieldsOf(
                                "supplier_name", "江南优品",
                                "item_description", "PE 快递袋 250x350mm 60um",
                                "material", "PE", "color", "白色", "print_colors", 1,
                                "currency", "CNY", "unit_price", "0.53", "price_basis", 1,
                                "tax_rate", "0.13", "tax_included", true,
                                "shipping_fee", "0", "shipping_included", true,
                                "moq", 3000, "lead_time_days", 10, "supports_invoice", true,
                                "width_mm", "250", "length_mm", "350", "thickness_um", "60",
                                "payment_terms", "月结 30 天", "valid_until", "2099-12-31")),
                        quoteFields("华东优包", "华东优包-快递袋三补-报价.xlsx", fieldsOf(
                                "supplier_name", "华东优包",
                                "item_description", "PE 快递袋 250x350mm 60um",
                                "material", "PE", "color", "白色", "print_colors", 1,
                                "currency", "CNY", "unit_price", "0.50", "price_basis", 1,
                                "tax_rate", "0.13", "tax_included", true,
                                "shipping_fee", "0", "shipping_included", true,
                                "moq", 3000, "lead_time_days", 7, "supports_invoice", true,
                                "width_mm", "250", "length_mm", "350", "thickness_um", "60",
                                "payment_terms", "月结 30 天", "valid_until", "2099-12-31")),
                        quoteFields("北方包装", "北方包装-快递袋三补-报价.xlsx", fieldsOf(
                                "supplier_name", "北方包装",
                                "item_description", "PE 快递袋 250x350mm 60um",
                                "material", "PE", "color", "白色", "print_colors", 1,
                                "currency", "CNY", "unit_price", "0.56", "price_basis", 1,
                                "tax_rate", "0.13", "tax_included", true,
                                "shipping_fee", "0", "shipping_included", true,
                                "moq", 6000, "lead_time_days", 11, "supports_invoice", true,
                                "width_mm", "250", "length_mm", "350", "thickness_um", "60",
                                "payment_terms", "月结 30 天", "valid_until", "2099-12-31"))));
    }

    /**
     * 将一套 synthetic 历史业务推进到审批闭环终态：
     * 创建任务 → 导入报价 → 确定性比价快照 → 直接落 approved 决策（demo-seed actor）→
     * 生成订单草稿与供应商确认邮件工件（synthetic 标记）→ 按阶段派生订单与对账付款记录。
     */
    private void seedClosedLoopHistory(
            String scenario,
            String closedLoopStage,
            String title,
            String category,
            String itemName,
            String quantity,
            String unit,
            Map<String, Object> specifications,
            Map<String, Object> constraints,
            List<Map<String, Object>> quoteList) throws Exception {
        var request = new LinkedHashMap<String, Object>();
        request.put("schema_version", 1);
        request.put("title", title);
        request.put("category", category);
        request.put("item_name", itemName);
        request.put("quantity", quantity);
        request.put("unit", unit);
        request.put("specifications", specifications);
        request.put("constraints", constraints);
        var requirement = requirement(request);
        var detail = tasksService.createStructured(requirement, "demo:" + scenario);
        var taskId = String.valueOf(detail.get("id"));
        var runId = sha256hex(scenario + ":run");
        var sessionId = sha256hex(scenario + ":session");
        var task = tasks.findById(taskId).orElseThrow();
        task.bindAgent(sessionId, runId);
        task = tasks.saveAndFlush(task);

        for (var raw : quoteList) {
            importSyntheticQuote(taskId, scenario, text(raw.get("supplier")),
                    text(raw.get("filename")), map(raw.get("fields")));
        }
        task = tasks.findById(taskId).orElseThrow();
        var snapshot = comparison.analyze(task, runId);
        // useSnapshot 尚未 flush，当前 version 即快照创建时的任务版本
        var taskVersionAtSnapshot = task.getVersion();
        task = tasks.saveAndFlush(task);

        var result = snapshot.getResult();
        var recommended = text(result.get("recommended_quote_id"));
        if (recommended.isBlank()) {
            throw new IllegalStateException("closed-loop 演示场景没有合格报价：" + scenario);
        }
        var note = "synthetic 历史成交（演示数据）";
        var pending = PendingDecision.create(
                uuid(32),
                uuid(36),
                taskId,
                runId,
                taskVersionAtSnapshot,
                snapshot.getId(),
                snapshot.getInputSha256(),
                "approved",
                recommended,
                CanonicalJson.sha256(Map.of("note", note)));
        pending.approve(
                sha256hex(scenario + ":approval"),
                CanonicalJson.sha256(Map.of("synthetic", true)),
                "formal_java_confirmation",
                Instant.now());
        pending.complete();
        pendingDecisions.save(pending);
        var decision = decisions.save(ProcurementDecision.create(pending, note, "demo-seed"));
        // 重新加载任务（跨事务场景下实体可能已分离），以最新 version 落终态
        task = tasks.findById(taskId).orElseThrow();
        task.finalizeDecision(recommended, false);
        task = tasks.saveAndFlush(task);

        var winnerName = winnerSupplier(result, recommended);
        var orderText = "采购订单草稿（synthetic 演示数据）\n采购编号：" + task.getReference()
                + "\n供应商：" + winnerName + "\n报价证据：" + snapshot.getInputSha256() + "\n";
        var mailText = "主题：" + task.getReference() + " 供应商确认（synthetic 演示数据）\n\n"
                + winnerName + "：\n采购决定已完成，请人工核对订单条款后回复确认。\n";
        artifacts.store(
                "purchase_order_draft", taskId, task.getReference() + "-采购订单草稿.txt",
                "text/plain; charset=utf-8",
                new ByteArrayInputStream(orderText.getBytes(StandardCharsets.UTF_8)),
                Map.of("synthetic", true, "demo_scenario", scenario,
                        "decision_id", decision.getId(), "run_id", runId));
        artifacts.store(
                "supplier_confirmation_email", taskId, task.getReference() + "-供应商确认邮件.txt",
                "text/plain; charset=utf-8",
                new ByteArrayInputStream(mailText.getBytes(StandardCharsets.UTF_8)),
                Map.of("synthetic", true, "demo_scenario", scenario,
                        "decision_id", decision.getId(), "run_id", runId));
        audit.save(AuditEvent.create(
                taskId, recommended, runId, "demo_seed_approved", "demo-seed",
                Map.of(
                        "scenario", scenario,
                        "synthetic", true,
                        "decision_id", decision.getId(),
                        "quote_id", recommended,
                        "snapshot_id", snapshot.getId(),
                        "supplier_name", winnerName,
                        "landed_total_base", winnerCost(result, recommended),
                        "item_name", task.getItemName())));
        task = tasks.findById(taskId).orElseThrow();
        applyClosedLoopStage(task, closedLoopStage, scenario);
        log.info("历史业务种子已预置（审批闭环）：{}（成交 {}，{}）", scenario, winnerName, title);
    }

    /**
     * 对 closed-loop 订单按演示阶段推进：shipped=已发货；settled=已收货+已对账；
     * paid=已收货+已对账+已付款。全部经订单/对账状态机流转，demo-seed actor 写审计。
     */
    private void applyClosedLoopStage(ProcurementTask task, String stage, String scenario) {
        var order = orderService.ensureOrderForApprovedTask(task);
        if (order == null) {
            return;
        }
        if ("shipped".equals(stage)) {
            orderService.transition(order.getId(), "ship", null, null, null, "demo-seed");
            return;
        }
        var receivedAt = Instant.parse("2026-07-20T08:00:00Z");
        orderService.transition(order.getId(), "ship", null, null, null, "demo-seed");
        orderService.transition(order.getId(), "receive", order.getQuantity(), receivedAt, null, "demo-seed");
        ensureSyntheticReconciledInvoice(order, scenario);
        var settlement = settlements.findByOrderId(order.getId()).orElseThrow();
        if ("settled".equals(stage)) {
            settlementService.transition(settlement.getId(), "settle", null, null, "demo-seed");
            return;
        }
        settlementService.transition(settlement.getId(), "settle", null, null, "demo-seed");
        settlementService.transition(settlement.getId(), "pay",
                Instant.parse("2026-07-25T10:00:00Z"), "synthetic 演示付款", "demo-seed");
    }

    private void ensureSyntheticReconciledInvoice(PurchaseOrder order, String scenario) {
        if (!invoices.findByOrderIdOrderByCreatedAtAsc(order.getId()).isEmpty()) {
            return;
        }
        var invoiceNo = "DEMO-INV-" + sha256hex(scenario).substring(0, 16).toUpperCase(java.util.Locale.ROOT);
        var content = ("synthetic 演示发票\n订单：" + order.getOrderNo()
                + "\n供应商：" + order.getSupplierName()
                + "\n含税总额：" + order.getLandedTotal().toPlainString() + "\n")
                .getBytes(StandardCharsets.UTF_8);
        var artifact = artifacts.store(
                "invoice_original", order.getId(), invoiceNo + ".txt",
                "text/plain; charset=utf-8", new ByteArrayInputStream(content),
                Map.of("synthetic", true, "demo_scenario", scenario, "order_id", order.getId()));
        var invoice = Invoice.register(
                order.getId(), invoiceNo, null, LocalDate.parse("2026-07-21"),
                order.getQuantity(), order.getUnit(),
                order.getLandedTotal().divide(order.getQuantity(), 18, java.math.RoundingMode.HALF_UP),
                order.getLandedTotal(), BigDecimal.ZERO, order.getLandedTotal(), BigDecimal.ZERO,
                order.getSupplierName(), artifact.getId(), artifact.getSha256(), "demo-seed-v1");
        invoice.applyMatchResult(true, Map.of(
                "matched", true,
                "diffs", List.of(),
                "synthetic", true), null);
        invoice.reconcile();
        invoices.saveAndFlush(invoice);
        audit.save(AuditEvent.create(
                order.getTaskId(), null, null,
                "invoice", invoice.getId(), "invoice_reconciled", "demo-seed",
                Map.of("invoice_id", invoice.getId(), "invoice_no", invoiceNo,
                        "order_id", order.getId(), "synthetic", true)));
    }

    @SuppressWarnings("unchecked")
    private String winnerSupplier(Map<String, Object> result, String quoteId) {
        for (var raw : list(result.get("quotes"))) {
            var quote = map(raw);
            if (quoteId.equals(text(quote.get("quote_id")))) {
                return text(quote.get("supplier_name"));
            }
        }
        return "";
    }

    @SuppressWarnings("unchecked")
    private String winnerCost(Map<String, Object> result, String quoteId) {
        for (var raw : list(result.get("quotes"))) {
            var quote = map(raw);
            if (quoteId.equals(text(quote.get("quote_id"))) && quote.get("cost") instanceof Map<?, ?> cost) {
                return text(((Map<?, ?>) cost).get("landed_total_base"));
            }
        }
        return "";
    }

    private Map<String, Object> quoteFields(String supplier, String filename, Map<String, Object> fields) {
        var value = new LinkedHashMap<String, Object>();
        value.put("supplier", supplier);
        value.put("filename", filename);
        value.put("fields", fields);
        return value;
    }

    /** 构建报价字段映射（Map.of 上限 10 对，报价字段更多）。 */
    private Map<String, Object> fieldsOf(Object... entries) {
        var fields = new LinkedHashMap<String, Object>();
        for (int index = 0; index + 1 < entries.length; index += 2) {
            fields.put(String.valueOf(entries[index]), entries[index + 1]);
        }
        return fields;
    }

    private void importSyntheticQuote(
            String taskId, String scenario, String supplier, String filename, Map<String, Object> fields)
            throws Exception {
        var artifact = artifacts.store(
                "procurement_original",
                taskId,
                filename,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                new ByteArrayInputStream(("synthetic:" + scenario + ":" + filename).getBytes(StandardCharsets.UTF_8)),
                Map.of("synthetic", true, "demo_scenario", scenario));
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
                "xlsx",
                artifact.getSha256(),
                extracted,
                "ready",
                "demo-seed-v1",
                BigDecimal.ZERO));
    }

    private String uuid(int chars) {
        if (chars >= 36) {
            return UUID.randomUUID().toString();
        }
        return UUID.randomUUID().toString().replace("-", "").substring(0, chars);
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
