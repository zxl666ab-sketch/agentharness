package com.caijiatai.procurement;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.caijiatai.procurement.agent.AgentDispatcher;
import com.caijiatai.procurement.agent.AgentCommandRepository;
import com.caijiatai.procurement.agent.AgentOutboxWorker;
import com.caijiatai.procurement.agent.AgentResultApplication;
import com.caijiatai.procurement.agent.CanonicalJson;
import com.caijiatai.procurement.agent.IdempotencyRecordRepository;
import com.caijiatai.procurement.ai.AiResult;
import com.caijiatai.procurement.ai.AiResultRepository;
import com.caijiatai.procurement.ai.AiTask;
import com.caijiatai.procurement.ai.AiTaskRepository;
import com.caijiatai.procurement.ai.AiTaskService;
import com.caijiatai.procurement.ai.AiTaskStatus;
import com.caijiatai.procurement.ai.AiTaskType;
import com.caijiatai.procurement.ai.AiErrorCategory;
import com.caijiatai.procurement.api.ApiException;
import com.caijiatai.procurement.approval.ApprovalService;
import com.caijiatai.procurement.approval.PendingDecision;
import com.caijiatai.procurement.approval.PendingDecisionRepository;
import com.caijiatai.procurement.approval.ProcurementDecisionRepository;
import com.caijiatai.procurement.artifact.ArtifactStore;
import com.caijiatai.procurement.comparison.ComparisonService;
import com.caijiatai.procurement.comparison.ComparisonSnapshotRepository;
import com.caijiatai.procurement.quote.ProcurementQuote;
import com.caijiatai.procurement.quote.ProcurementQuoteRepository;
import com.caijiatai.procurement.report.ProcurementReportService;
import com.caijiatai.procurement.review.ReviewAction;
import com.caijiatai.procurement.review.ReviewDtos;
import com.caijiatai.procurement.review.ReviewRecord;
import com.caijiatai.procurement.review.ReviewRecordRepository;
import com.caijiatai.procurement.review.ReviewService;
import com.caijiatai.procurement.review.ReviewStatus;
import com.caijiatai.procurement.task.ProcurementDtos;
import com.caijiatai.procurement.task.ProcurementTask;
import com.caijiatai.procurement.task.ProcurementTaskRepository;
import com.caijiatai.procurement.task.ProcurementTaskService;
import com.caijiatai.procurement.task.TaskStatus;
import jakarta.persistence.EntityManagerFactory;
import jakarta.persistence.OptimisticLockException;
import jakarta.persistence.RollbackException;
import java.io.ByteArrayInputStream;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.mysql.MySQLContainer;

@Testcontainers
@SpringBootTest(properties = {
        "app.agent-mode=demo",
        "app.internal-hmac-key=test-hmac-key-for-spring-context-0123456789abcdef",
        "app.artifact-root=target/test-artifacts",
        "app.outbox.enabled=false"
})
class MySqlIntegrationTest {
    private static final String SESSION_ID = "a".repeat(32);
    private static final String RUN_ID = "b".repeat(32);

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
    @Autowired ComparisonSnapshotRepository snapshots;
    @Autowired PendingDecisionRepository pendingDecisions;
    @Autowired ProcurementDecisionRepository decisions;
    @Autowired AgentCommandRepository commands;
    @Autowired IdempotencyRecordRepository idempotency;
    @Autowired AiTaskRepository aiTasks;
    @Autowired AiResultRepository aiResults;
    @Autowired AiTaskService aiTaskService;
    @Autowired ProcurementTaskService taskService;
    @Autowired ComparisonService comparisonService;
    @Autowired ApprovalService approvalService;
    @Autowired ArtifactStore artifactStore;
    @Autowired AgentResultApplication resultApplication;
    @Autowired ProcurementReportService reportService;
    @Autowired ReviewRecordRepository reviewRecords;
    @Autowired ReviewService reviewService;
    @Autowired EntityManagerFactory entityManagerFactory;
    @Autowired PlatformTransactionManager transactionManager;

    TransactionTemplate transactions;

    @BeforeEach
    void cleanDatabase() {
        transactions = new TransactionTemplate(transactionManager);
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
    void flywayCreatesMysqlTypesAndJpaPersistsOptimisticTask() {
        var columns = jdbc.queryForList(
                """
                select column_name, data_type from information_schema.columns
                where table_schema = database() and table_name = 'procurement_task'
                """);
        assertThat(columns).anySatisfy(row -> {
            assertThat(row.get("column_name")).isEqualTo("specifications");
            assertThat(row.get("data_type")).isEqualTo("json");
        });
        assertThat(columns).anySatisfy(row -> {
            assertThat(row.get("column_name")).isEqualTo("quantity");
            assertThat(row.get("data_type")).isEqualTo("decimal");
        });

        var saved = tasks.saveAndFlush(newTask());
        assertThat(saved.getVersion()).isZero();
        saved.setStatus(TaskStatus.READY);
        saved = tasks.saveAndFlush(saved);
        assertThat(saved.getVersion()).isEqualTo(1);
    }

    @Test
    void aiTaskDomainEnforcesOwnershipGenerationIdempotencyAndSingleResult() {
        var task = tasks.saveAndFlush(newTask());
        var inputSha = "1".repeat(64);
        var first = aiTasks.saveAndFlush(AiTask.create(
                task.getId(), AiTaskType.QUOTE_ANALYSIS, 1, task.getVersion(),
                inputSha, "same-request", "采购员", 3));
        var nextGeneration = aiTasks.saveAndFlush(AiTask.create(
                task.getId(), AiTaskType.QUOTE_ANALYSIS, 2, task.getVersion(),
                "2".repeat(64), "same-request", "采购员", 3));

        assertThat(nextGeneration.getGeneration()).isEqualTo(2);
        assertThat(aiTasks.findByBusinessIdOrderByCreatedAtDesc(task.getId())).hasSize(2);
        assertThatThrownBy(() -> transactions.executeWithoutResult(ignored ->
                aiTasks.saveAndFlush(AiTask.create(
                        task.getId(), AiTaskType.QUOTE_ANALYSIS, 1, task.getVersion(),
                        inputSha, "same-request", "采购员", 3))))
                .isInstanceOf(DataIntegrityViolationException.class);
        assertThatThrownBy(() -> transactions.executeWithoutResult(ignored ->
                aiTasks.saveAndFlush(AiTask.create(
                        "f".repeat(32), AiTaskType.QUOTE_ANALYSIS, 1, 0,
                        inputSha, "unknown-business", "采购员", 3))))
                .isInstanceOf(DataIntegrityViolationException.class);

        var result = aiResults.saveAndFlush(AiResult.create(
                first.getId(), task.getId(), 1, inputSha,
                Map.of("summary", "raw"),
                Map.of("summary", "structured"),
                List.of(),
                "procurement_fake", "deterministic", "quote-analysis-v1", "parser-v3"));
        assertThat(result.getResultSha256()).hasSize(64);
        assertThatThrownBy(() -> transactions.executeWithoutResult(ignored ->
                aiResults.saveAndFlush(AiResult.create(
                        first.getId(), task.getId(), 1, inputSha,
                        Map.of("summary", "other"),
                        Map.of("summary", "other"),
                        List.of(),
                        "procurement_fake", "deterministic", "quote-analysis-v1", "parser-v3"))))
                .isInstanceOf(DataIntegrityViolationException.class);
    }

    @Test
    void persistentIdempotencyReplaysAndRejectsChangedPayload() {
        var first = taskService.createStructured(requirement("10"), "stable-create-key");
        var replay = taskService.createStructured(requirement("10.0"), "stable-create-key");

        assertThat(replay.get("id")).isEqualTo(first.get("id"));
        assertThat(tasks.count()).isEqualTo(1);
        assertThat(commands.count()).isEqualTo(1);
        assertThat(idempotency.count()).isEqualTo(1);

        assertThatThrownBy(() -> taskService.createStructured(requirement("11"), "stable-create-key"))
                .isInstanceOfSatisfying(ApiException.class, error -> {
                    assertThat(error.code()).isEqualTo("idempotency_payload_conflict");
                    assertThat(error.status().value()).isEqualTo(409);
                });
        assertThat(tasks.count()).isEqualTo(1);
        assertThat(commands.count()).isEqualTo(1);
    }

    @Test
    void duplicateAnalysisSchedulingReturnsTheOriginalOperation() {
        var taskId = preparedAnalyzableTask();

        var first = taskService.analyze(taskId, "analyze-once");
        var replay = taskService.analyze(taskId, "analyze-once");

        assertThat(replay.operationId()).isEqualTo(first.operationId());
        assertThat(commands.findAll()).extracting(command -> command.getOperationType())
                .containsExactly("analyze");
        assertThat(aiTasks.count()).isEqualTo(1);
        assertThat(tasks.findById(taskId).orElseThrow().getStatus()).isEqualTo("ready");
    }

    @Test
    void aiAnalysisPersistsStepsExplanationAndDeterministicSnapshot() {
        var taskId = preparedAnalyzableTask();
        var accepted = taskService.analyze(taskId, "ai-analysis-once");
        var command = commands.findById(accepted.operationId()).orElseThrow();
        var aiTask = aiTasks.findByOperationId(command.getOperationId()).orElseThrow();

        assertThat(tasks.findById(taskId).orElseThrow().getStatus()).isEqualTo("ready");
        assertThat(aiTask.getStatus()).isEqualTo(AiTaskStatus.PENDING);
        assertThat(aiTask.getInputSha256()).isEqualTo(command.getPayload().get("input_sha256"));

        aiTaskService.markDispatching(command);
        aiTaskService.applyStepEvent(Map.ofEntries(
                Map.entry("ai_task_id", aiTask.getId()),
                Map.entry("operation_id", command.getOperationId()),
                Map.entry("attempt", 1),
                Map.entry("sequence", 1),
                Map.entry("step", "INPUT_VALIDATE"),
                Map.entry("step_status", "RUNNING"),
                Map.entry("progress", "0.02"),
                Map.entry("summary", "校验输入"),
                Map.entry("occurred_at", "2026-08-12T12:00:00Z")));
        assertThat(aiTasks.findById(aiTask.getId()).orElseThrow().getStatus())
                .isEqualTo(AiTaskStatus.RUNNING);

        var client = mock(AgentDispatcher.class);
        when(client.dispatch(any())).thenReturn(new AgentDispatcher.DispatchResult(200, Map.of(
                "status", "completed",
                "result", Map.ofEntries(
                        Map.entry("run_id", RUN_ID),
                        Map.entry("input_sha256", command.getPayload().get("input_sha256")),
                        Map.entry("structured_result", Map.of(
                                "summary", "已核对两份报价，确定性计算由 Java 完成",
                                "risk_flags", List.of())),
                        Map.entry("sources", List.of()),
                        Map.entry("provider", "procurement_fake"),
                        Map.entry("model", "deterministic"),
                        Map.entry("prompt_version", "quote-analysis-v1"),
                        Map.entry("parser_version", "packaging-quote-v3")))));

        transactions.executeWithoutResult(ignored ->
                new AgentOutboxWorker(commands, tasks, client, resultApplication, aiTaskService).dispatch());

        var completedTask = aiTasks.findById(aiTask.getId()).orElseThrow();
        var detail = aiTaskService.detail(aiTask.getId());
        assertThat(completedTask.getStatus()).isEqualTo(AiTaskStatus.SUCCEEDED);
        assertThat(completedTask.getProgress()).isEqualByComparingTo("1");
        assertThat(tasks.findById(taskId).orElseThrow().getStatus()).isEqualTo("analyzed");
        assertThat(snapshots.findFirstByTaskIdOrderBySnapshotVersionDesc(taskId)).isPresent();
        assertThat(aiResults.findByAiTaskId(aiTask.getId())).isPresent();
        assertThat((List<?>) detail.get("records")).hasSize(2);
        var persistedResult = (Map<?, ?>) detail.get("result");
        assertThat(persistedResult.containsKey("ai_result_id")).isTrue();
        assertThat(persistedResult.containsKey("input_sha256")).isTrue();
        assertThat(persistedResult.containsKey("result_sha256")).isTrue();
        assertThat(persistedResult.containsKey("structured_result")).isTrue();
    }

    @Test
    void analysisRequiresAPersistedRequirementConfirmation() {
        var taskId = transactions.execute(ignored -> {
            var task = newTask();
            task.requireRequirementReview();
            task.bindAgent(SESSION_ID, RUN_ID);
            task.setStatus(TaskStatus.REVIEW);
            task = tasks.saveAndFlush(task);
            addQuote(task.getId(), "Alpha Packaging", "520", "alpha");
            addQuote(task.getId(), "Beta Packaging", "600", "beta");
            return task.getId();
        });

        assertThatThrownBy(() -> taskService.analyze(taskId, "review-gate"))
                .isInstanceOfSatisfying(ApiException.class, error ->
                        assertThat(error.code()).isEqualTo("requirement_review_required"));

        taskService.correctRequirement(taskId, requirement("10000"));

        assertThat(tasks.findById(taskId).orElseThrow().isRequirementConfirmed()).isTrue();
        assertThat(taskService.analyze(taskId, "review-gate-after-confirm").operationId()).isNotBlank();
    }

    @Test
    void terminalAnalysisFailureRestoresTheTaskForCorrectionAndRetry() {
        var taskId = transactions.execute(ignored -> {
            var task = newTask();
            task.bindAgent(SESSION_ID, RUN_ID);
            task.setStatus(TaskStatus.READY);
            task = tasks.saveAndFlush(task);
            addQuote(task.getId(), "Alpha Packaging", "520", "alpha", "CNY");
            addQuote(task.getId(), "Dollar Packaging", "80", "dollar", "USD");
            return task.getId();
        });
        var accepted = taskService.analyze(taskId, "missing-fx-analysis");
        var client = mock(AgentDispatcher.class);
        when(client.dispatch(any())).thenReturn(new AgentDispatcher.DispatchResult(200, Map.of(
                "status", "completed", "result", Map.of("run_id", RUN_ID))));

        transactions.executeWithoutResult(ignored ->
                new AgentOutboxWorker(commands, tasks, client, resultApplication, aiTaskService).dispatch());

        assertThat(commands.findById(accepted.operationId()).orElseThrow().getStatus()).isEqualTo("failed");
        assertThat(commands.findById(accepted.operationId()).orElseThrow().getLastError())
                .contains("缺少 USD 汇率");
        assertThat(tasks.findById(taskId).orElseThrow().getStatus()).isEqualTo("ready");
    }

    @Test
    void agentOutageExhaustsDeliveryThenManualRetryRecoversTheSameAiTask() {
        var taskId = preparedAnalyzableTask();
        var accepted = taskService.analyze(taskId, "outage-analysis");
        var firstOperationId = accepted.operationId();
        var aiTaskId = aiTasks.findByOperationId(firstOperationId).orElseThrow().getId();
        var unavailable = mock(AgentDispatcher.class);
        when(unavailable.dispatch(any())).thenThrow(new AgentDispatcher.AgentUnavailableException(
                "Agent unavailable", new IllegalStateException("stopped")));
        var worker = new AgentOutboxWorker(
                commands, tasks, unavailable, resultApplication, aiTaskService);

        for (var attempt = 1; attempt <= 4; attempt++) {
            transactions.executeWithoutResult(ignored -> worker.dispatch());
            if (attempt < 4) {
                jdbc.update(
                        "update agent_command set next_attempt_at = date_sub(now(), interval 1 second) "
                                + "where operation_id = ?",
                        firstOperationId);
            }
        }

        var failedCommand = commands.findById(firstOperationId).orElseThrow();
        var failedTask = aiTasks.findById(aiTaskId).orElseThrow();
        assertThat(failedCommand.getStatus()).isEqualTo("failed");
        assertThat(failedCommand.getAttemptCount()).isEqualTo(4);
        assertThat(failedTask.getStatus()).isEqualTo(AiTaskStatus.FAILED);
        assertThat(failedTask.getErrorCategory()).isEqualTo(AiErrorCategory.TRANSPORT);
        assertThat(failedTask.isRetryable()).isTrue();
        assertThat(tasks.findById(taskId).orElseThrow().getStatus()).isEqualTo("ready");

        var retry = aiTaskService.retry(aiTaskId, "manual-retry-key", false);
        var replay = aiTaskService.retry(aiTaskId, "manual-retry-key", false);
        assertThat(replay.getOperationId()).isEqualTo(retry.getOperationId());
        assertThat(retry.getOperationId()).isNotEqualTo(firstOperationId);
        assertThat(retry.getRetryCount()).isEqualTo(1);
        assertThat(commands.count()).isEqualTo(2);

        var retryCommand = commands.findById(retry.getOperationId()).orElseThrow();
        var recovered = mock(AgentDispatcher.class);
        when(recovered.dispatch(any())).thenReturn(analysisResponse(retryCommand));
        transactions.executeWithoutResult(ignored -> new AgentOutboxWorker(
                commands, tasks, recovered, resultApplication, aiTaskService).dispatch());

        assertThat(aiTasks.findById(aiTaskId).orElseThrow().getStatus())
                .isEqualTo(AiTaskStatus.SUCCEEDED);
        assertThat(aiResults.findByAiTaskId(aiTaskId)).isPresent();
        assertThat(tasks.findById(taskId).orElseThrow().getStatus()).isEqualTo("analyzed");
    }

    @Test
    void cancelIsIdempotentAndLateResultCannotMutateBusinessState() {
        var taskId = preparedAnalyzableTask();
        var accepted = taskService.analyze(taskId, "cancel-analysis");
        var command = commands.findById(accepted.operationId()).orElseThrow();
        var aiTask = aiTasks.findByOperationId(command.getOperationId()).orElseThrow();

        var cancelled = aiTaskService.cancel(aiTask.getId(), "cancel-once-key");
        var replay = aiTaskService.cancel(aiTask.getId(), "cancel-once-key");
        assertThat(cancelled.getStatus()).isEqualTo(AiTaskStatus.CANCELLED);
        assertThat(replay.getStatus()).isEqualTo(AiTaskStatus.CANCELLED);
        assertThat(commands.findById(command.getOperationId()).orElseThrow().getStatus())
                .isEqualTo("cancelled");

        // A result arriving after cancellation is not dispatched or applied.
        transactions.executeWithoutResult(ignored -> new AgentOutboxWorker(
                commands, tasks, mock(AgentDispatcher.class), resultApplication, aiTaskService).dispatch());
        assertThat(snapshots.findFirstByTaskIdOrderBySnapshotVersionDesc(taskId)).isEmpty();
        assertThat(aiResults.findByAiTaskId(aiTask.getId())).isEmpty();
        assertThat(tasks.findById(taskId).orElseThrow().getStatus()).isEqualTo("ready");
    }

    @Test
    void procurementCorrectionMarksHistoricalAiTaskAndResultStale() {
        var taskId = preparedAnalyzableTask();
        var accepted = taskService.analyze(taskId, "stale-analysis");
        var command = commands.findById(accepted.operationId()).orElseThrow();
        var aiTaskId = aiTasks.findByOperationId(command.getOperationId()).orElseThrow().getId();
        var client = mock(AgentDispatcher.class);
        when(client.dispatch(any())).thenReturn(analysisResponse(command));
        transactions.executeWithoutResult(ignored -> new AgentOutboxWorker(
                commands, tasks, client, resultApplication, aiTaskService).dispatch());

        taskService.correctRequirement(taskId, requirement("10001"));

        var staleTask = aiTasks.findById(aiTaskId).orElseThrow();
        var staleResult = aiResults.findByAiTaskId(aiTaskId).orElseThrow();
        assertThat(staleTask.getStatus()).isEqualTo(AiTaskStatus.SUCCEEDED);
        assertThat(staleTask.isStale()).isTrue();
        assertThat(staleTask.getStaleReason()).isEqualTo("INPUT_GENERATION_CHANGED");
        assertThat(staleResult.isStale()).isTrue();
        assertThat(staleResult.getStaleReason()).isEqualTo("INPUT_GENERATION_CHANGED");
        assertThat(tasks.findById(taskId).orElseThrow().getCurrentSnapshotId()).isNull();
    }

    @Test
    void approveSuggestionKeepsAiAdviceImmutableAndFinalizesExactlyOnce() {
        var fixture = pendingReview(false);
        var initialDetail = reviewService.detail(fixture.review().getId());
        var comparisonDetail = (Map<?, ?>) initialDetail.get("comparison");
        assertThat(comparisonDetail.get("id")).isEqualTo(fixture.review().getSnapshotId());
        assertThat(comparisonDetail.get("input_sha256")).isEqualTo(fixture.review().getInputSha256());
        assertThat(initialDetail)
                .containsEntry("generation", fixture.review().getGeneration())
                .containsEntry("snapshot_id", fixture.review().getSnapshotId())
                .containsEntry("decision_id", null);
        var originalResult = Map.copyOf(aiResults.findById(fixture.review().getAiResultId())
                .orElseThrow().getStructuredResult());
        var request = new ReviewDtos.ActionRequest(
                ReviewAction.APPROVE_SUGGESTION,
                fixture.review().getVersion(),
                "采购员甲",
                null,
                Map.of(),
                "已核对报价原件与到货成本");

        reviewService.action(fixture.review().getId(), request, "review-approve-once");
        reviewService.action(fixture.review().getId(), request, "review-approve-once");
        completeReviewDecision(fixture.review().getId());

        var completed = reviewRecords.findById(fixture.review().getId()).orElseThrow();
        assertThat(completed.getStatus()).isEqualTo(ReviewStatus.APPROVED);
        assertThat(completed.getAction()).isEqualTo(ReviewAction.APPROVE_SUGGESTION);
        assertThat(completed.getFinalQuoteId()).isEqualTo(completed.getSuggestedQuoteId());
        assertThat(completed.getEvidenceSha256()).hasSize(64);
        assertThat(decisions.count()).isEqualTo(1);
        assertThat(aiResults.findById(completed.getAiResultId()).orElseThrow().getStructuredResult())
                .isEqualTo(originalResult);
    }

    @Test
    void reviseAndApproveStoresHumanValuesWithoutOverwritingTheSuggestion() {
        var fixture = pendingReview(false);
        var snapshot = snapshots.findById(fixture.review().getSnapshotId()).orElseThrow();
        var alternative = ((List<?>) snapshot.getResult().get("quotes")).stream()
                .map(item -> (Map<?, ?>) item)
                .filter(item -> Boolean.TRUE.equals(item.get("eligible")))
                .map(item -> String.valueOf(item.get("quote_id")))
                .filter(id -> !id.equals(fixture.review().getSuggestedQuoteId()))
                .findFirst().orElseThrow();
        var suggestion = fixture.review().getSuggestedQuoteId();

        reviewService.action(
                fixture.review().getId(),
                new ReviewDtos.ActionRequest(
                        ReviewAction.REVISE_AND_APPROVE,
                        fixture.review().getVersion(),
                        "采购员乙",
                        alternative,
                        Map.of("supplier_selection", Map.of("from", suggestion, "to", alternative)),
                        "交期与付款条件更适合"),
                "review-revise-once");
        completeReviewDecision(fixture.review().getId());

        var completed = reviewRecords.findById(fixture.review().getId()).orElseThrow();
        assertThat(completed.getStatus()).isEqualTo(ReviewStatus.APPROVED);
        assertThat(completed.getSuggestedQuoteId()).isEqualTo(suggestion);
        assertThat(completed.getFinalQuoteId()).isEqualTo(alternative);
        assertThat(completed.getRevisions()).containsKey("supplier_selection");
        assertThat(decisions.findByTaskId(fixture.taskId()).orElseThrow().getQuoteId())
                .isEqualTo(alternative);
    }

    @Test
    void rejectAndRetryIsIdempotentAndReturnsTheTaskToAnalysis() {
        var fixture = pendingReview(false);
        var request = new ReviewDtos.ActionRequest(
                ReviewAction.REJECT_AND_RETRY,
                fixture.review().getVersion(),
                "采购员丙",
                null,
                Map.of(),
                "报价来源需要补充核验");

        reviewService.action(fixture.review().getId(), request, "review-reject-once");
        reviewService.action(fixture.review().getId(), request, "review-reject-once");

        var rejected = reviewRecords.findById(fixture.review().getId()).orElseThrow();
        var business = tasks.findById(fixture.taskId()).orElseThrow();
        assertThat(rejected.getStatus()).isEqualTo(ReviewStatus.REJECTED);
        assertThat(rejected.getReason()).isEqualTo("报价来源需要补充核验");
        assertThat(business.getStatus()).isEqualTo("ready");
        assertThat(business.getCurrentSnapshotId()).isNull();
        assertThat(pendingDecisions.count()).isZero();
        assertThat(decisions.count()).isZero();
    }

    @Test
    void noAwardRequiresAllQuotesExcludedAndCreatesNoSupplierDecision() {
        var fixture = pendingReview(true);
        assertThat(fixture.review().getSuggestedQuoteId()).isNull();
        assertThat(fixture.review().getRiskFlags()).contains("NO_ELIGIBLE_QUOTES");

        reviewService.action(
                fixture.review().getId(),
                new ReviewDtos.ActionRequest(
                        ReviewAction.NO_AWARD,
                        fixture.review().getVersion(),
                        "采购员丁",
                        null,
                        Map.of(),
                        "全部报价均超过交期上限"),
                "review-no-award");
        completeReviewDecision(fixture.review().getId());

        var completed = reviewRecords.findById(fixture.review().getId()).orElseThrow();
        var decision = decisions.findByTaskId(fixture.taskId()).orElseThrow();
        assertThat(completed.getStatus()).isEqualTo(ReviewStatus.NO_AWARD);
        assertThat(decision.getDecision()).isEqualTo("no_award");
        assertThat(decision.getQuoteId()).isNull();
        assertThat(tasks.findById(fixture.taskId()).orElseThrow().getStatus()).isEqualTo("no_award");
    }

    @Test
    void staleAndConcurrentReviewEvidenceCannotBeSubmitted() {
        var fixture = pendingReview(false);
        assertThatThrownBy(() -> reviewService.action(
                fixture.review().getId(),
                new ReviewDtos.ActionRequest(
                        ReviewAction.APPROVE_SUGGESTION,
                        fixture.review().getVersion() + 1,
                        "采购员戊",
                        null,
                        Map.of(),
                        "旧页面提交"),
                "review-wrong-version"))
                .isInstanceOfSatisfying(ApiException.class, error ->
                        assertThat(error.code()).isEqualTo("review_version_conflict"));

        taskService.correctRequirement(fixture.taskId(), requirement("10001"));
        var stale = reviewRecords.findById(fixture.review().getId()).orElseThrow();
        assertThat(stale.getStatus()).isEqualTo(ReviewStatus.STALE);
        assertThat(stale.getStaleReason()).isEqualTo("INPUT_GENERATION_CHANGED");
        assertThat(aiResults.findById(stale.getAiResultId()).orElseThrow().isStale()).isTrue();
        assertThatThrownBy(() -> reviewService.action(
                stale.getId(),
                new ReviewDtos.ActionRequest(
                        ReviewAction.APPROVE_SUGGESTION,
                        stale.getVersion(),
                        "采购员戊",
                        null,
                        Map.of(),
                        "旧证据提交"),
                "review-stale-evidence"))
                .isInstanceOf(ApiException.class);
        assertThat(decisions.count()).isZero();
    }

    @Test
    void acceptedResponseIsReplayedByANewWorkerWithTheSameOperationId() {
        var detail = taskService.createStructured(requirement("10"), "response-loss");
        var taskId = String.valueOf(detail.get("id"));
        var operationId = commands.findAll().getFirst().getOperationId();
        var client = mock(AgentDispatcher.class);
        when(client.dispatch(any())).thenReturn(
                new AgentDispatcher.DispatchResult(202, Map.of("status", "accepted")),
                new AgentDispatcher.DispatchResult(200, Map.of(
                        "status", "completed",
                        "result", Map.of("session_id", SESSION_ID, "run_id", RUN_ID))));

        transactions.executeWithoutResult(ignored ->
                new AgentOutboxWorker(commands, tasks, client, resultApplication, aiTaskService).dispatch());
        assertThat(commands.findById(operationId).orElseThrow().getStatus()).isEqualTo("accepted");
        jdbc.update(
                "update agent_command set next_attempt_at = date_sub(now(), interval 1 second) where operation_id = ?",
                operationId);

        transactions.executeWithoutResult(ignored ->
                new AgentOutboxWorker(commands, tasks, client, resultApplication, aiTaskService).dispatch());

        var recovered = tasks.findById(taskId).orElseThrow();
        assertThat(recovered.getSessionId()).isEqualTo(SESSION_ID);
        assertThat(recovered.getAnalysisRunId()).isEqualTo(RUN_ID);
        assertThat(commands.findById(operationId).orElseThrow().getStatus()).isEqualTo("completed");
        verify(client, times(2)).dispatch(any());
    }

    @Test
    void correctionInvalidatesPendingApprovalAndRejectsLateAgentEvidence() {
        var fixture = pendingApproval();

        taskService.correctRequirement(fixture.taskId(), requirement("10001"));

        assertThat(pendingDecisions.findById(fixture.pending().getId()).orElseThrow().getStatus())
                .isEqualTo("stale");
        assertThat(tasks.findById(fixture.taskId()).orElseThrow().getCurrentSnapshotId()).isNull();
        assertThatThrownBy(() -> approvalService.finalizeFromAgent(fixture.command(), fixture.agentResult()))
                .isInstanceOfSatisfying(ApiException.class, error ->
                        assertThat(error.code()).isEqualTo("stale_approval"));
        assertThat(decisions.findByTaskId(fixture.taskId())).isEmpty();
    }

    @Test
    void duplicateFinalizationReturnsTheOriginalDecision() {
        var fixture = pendingApproval();

        var first = approvalService.finalizeFromAgent(fixture.command(), fixture.agentResult());
        var replay = approvalService.finalizeFromAgent(fixture.command(), fixture.agentResult());

        assertThat(replay.getId()).isEqualTo(first.getId());
        assertThat(decisions.count()).isEqualTo(1);
        assertThat(tasks.findById(fixture.taskId()).orElseThrow().getStatus()).isEqualTo("approved");
        assertThat(jdbc.queryForObject(
                "select count(*) from business_artifact where task_id = ? and kind in ('purchase_order_draft', 'supplier_confirmation_email')",
                Integer.class,
                fixture.taskId())).isEqualTo(2);
        var report = reportService.report(fixture.taskId());
        assertThat((List<?>) report.get("execution_artifacts")).hasSize(2);
        var history = (Map<?, ?>) report.get("supplier_history");
        assertThat((List<?>) history.get("suppliers")).anySatisfy(raw -> {
            var supplier = (Map<?, ?>) raw;
            assertThat(supplier.get("approved_purchase_count")).isEqualTo(1);
        });
    }

    @Test
    void transactionRollbackLeavesNoPartialTask() {
        var taskId = "f".repeat(32);
        assertThatThrownBy(() -> transactions.executeWithoutResult(ignored -> {
            var task = newTask();
            setTaskId(task, taskId);
            tasks.saveAndFlush(task);
            throw new IllegalStateException("force rollback");
        })).isInstanceOf(IllegalStateException.class);

        assertThat(tasks.findById(taskId)).isEmpty();
    }

    @Test
    void stalePersistenceContextCannotOverwriteANewerTaskVersion() {
        var task = tasks.saveAndFlush(newTask());
        var first = entityManagerFactory.createEntityManager();
        var second = entityManagerFactory.createEntityManager();
        try {
            first.getTransaction().begin();
            second.getTransaction().begin();
            var firstCopy = first.find(ProcurementTask.class, task.getId());
            var staleCopy = second.find(ProcurementTask.class, task.getId());
            firstCopy.setStatus(TaskStatus.READY);
            staleCopy.setStatus(TaskStatus.REVIEW);
            first.getTransaction().commit();

            assertThatThrownBy(second.getTransaction()::commit)
                    .isInstanceOf(RollbackException.class)
                    .hasCauseInstanceOf(OptimisticLockException.class);
        } finally {
            if (first.getTransaction().isActive()) first.getTransaction().rollback();
            if (second.getTransaction().isActive()) second.getTransaction().rollback();
            first.close();
            second.close();
        }
    }

    @Test
    void rejectsRequirementOutsideDomainBounds() {
        var overThicknessTolerance = new ProcurementDtos.Requirement(
                1, "测试采购", "ecommerce_packaging", "快递袋",
                new BigDecimal("10000"), "piece",
                Map.of("width_mm", "250", "length_mm", "350", "thickness_um", "60",
                        "material", "PE", "color", "白色", "print_colors", 1),
                new ProcurementDtos.Constraints(
                        "CNY", Map.of("CNY", BigDecimal.ONE), 15, true,
                        new BigDecimal("2"), new BigDecimal("6000"),
                        new BigDecimal("0.70"), "华东仓", null));
        assertThatThrownBy(() -> taskService.createStructured(overThicknessTolerance, "cap-thickness"))
                .isInstanceOfSatisfying(ApiException.class, error ->
                        assertThat(error.code()).isEqualTo("invalid_constraints"));

        var overSizeTolerance = new ProcurementDtos.Requirement(
                1, "测试采购", "ecommerce_packaging", "快递袋",
                new BigDecimal("10000"), "piece",
                Map.of("width_mm", "250", "length_mm", "350", "thickness_um", "60",
                        "material", "PE", "color", "白色", "print_colors", 1),
                new ProcurementDtos.Constraints(
                        "CNY", Map.of("CNY", BigDecimal.ONE), 15, true,
                        new BigDecimal("101"), new BigDecimal("3"),
                        new BigDecimal("0.70"), "华东仓", null));
        assertThatThrownBy(() -> taskService.createStructured(overSizeTolerance, "cap-size"))
                .isInstanceOfSatisfying(ApiException.class, error ->
                        assertThat(error.code()).isEqualTo("invalid_constraints"));

        var cartonWithoutHeight = new ProcurementDtos.Requirement(
                1, "苏州工厂出口瓦楞纸箱采购", "ecommerce_packaging", "五层瓦楞纸箱",
                new BigDecimal("5000"), "piece",
                Map.of("width_mm", "400", "length_mm", "300", "thickness_um", "5000",
                        "material", "瓦楞纸", "color", "牛皮色", "print_colors", 1),
                new ProcurementDtos.Constraints(
                        "CNY", Map.of("CNY", BigDecimal.ONE), 20, true,
                        new BigDecimal("5"), new BigDecimal("500"),
                        new BigDecimal("3.5"), "华东仓", null));
        assertThatThrownBy(() -> taskService.createStructured(cartonWithoutHeight, "carton-height"))
                .isInstanceOfSatisfying(ApiException.class, error ->
                        assertThat(error.code()).isEqualTo("invalid_specifications"));
    }

    @Test
    void createsAndPersistsV2DynamicRequirement() {
        var body = new ProcurementDtos.Requirement(
                2, "上海仓 BOPP 透明封箱胶带采购", "ecommerce_packaging", "BOPP透明封箱胶带",
                new BigDecimal("3000"), "roll", v2TapeSpecifications(),
                new ProcurementDtos.Constraints(
                        "CNY", Map.of("CNY", BigDecimal.ONE), 12, true,
                        null, null, new BigDecimal("4.20"), "上海仓", null));

        var created = taskService.createStructured(body, "v2-dynamic-create");
        var stored = tasks.findById(String.valueOf(created.get("id"))).orElseThrow();

        assertThat(created.get("schema_version")).isEqualTo(2);
        assertThat(created.get("unit")).isEqualTo("roll");
        assertThat(stored.getSchemaVersion()).isEqualTo(2);
        assertThat(stored.getSpecifications()).containsKeys(
                "width", "length", "thickness", "material", "color", "print_colors");
        @SuppressWarnings("unchecked")
        var length = (Map<String, Object>) stored.getSpecifications().get("length");
        assertThat(length)
                .containsEntry("value", "100")
                .containsEntry("unit", "m");
    }

    @Test
    void approvalFallsBackToSnapshotRunIdWhenTaskHasNoBoundRun() {
        var taskId = transactions.execute(ignored -> {
            var task = newTask();
            task.setStatus(TaskStatus.READY);
            task = tasks.saveAndFlush(task);
            addQuote(task.getId(), "Alpha Packaging", "520", "alpha");
            addQuote(task.getId(), "Beta Packaging", "600", "beta");
            return task.getId();
        });
        transactions.executeWithoutResult(ignored -> {
            var task = tasks.lockById(taskId).orElseThrow();
            comparisonService.analyze(task, RUN_ID);
        });
        var snapshot = snapshots.findFirstByTaskIdOrderBySnapshotVersionDesc(taskId).orElseThrow();
        assertThat(tasks.findById(taskId).orElseThrow().getAnalysisRunId()).isNull();
        var quoteId = String.valueOf(((List<?>) snapshot.getResult().get("quotes")).stream()
                .map(item -> (Map<?, ?>) item)
                .filter(item -> Boolean.TRUE.equals(item.get("eligible")))
                .findFirst().orElseThrow().get("quote_id"));

        var requested = approvalService.request(
                taskId,
                new ProcurementDtos.Decision(
                        "approved", snapshot.getId(), snapshot.getInputSha256(), quoteId, true, "已核对"),
                "approval-fallback");

        assertThat(requested.pending().getRunId()).isEqualTo(RUN_ID);
    }

    private PendingFixture pendingApproval() {
        var taskId = preparedAnalyzableTask();
        transactions.executeWithoutResult(ignored -> {
            var task = tasks.lockById(taskId).orElseThrow();
            comparisonService.analyze(task, RUN_ID);
        });
        var snapshot = snapshots.findFirstByTaskIdOrderBySnapshotVersionDesc(taskId).orElseThrow();
        var quoteId = String.valueOf(((List<?>) snapshot.getResult().get("quotes")).stream()
                .map(item -> (Map<?, ?>) item)
                .filter(item -> Boolean.TRUE.equals(item.get("eligible")))
                .findFirst().orElseThrow().get("quote_id"));
        var requested = approvalService.request(
                taskId,
                new ProcurementDtos.Decision(
                        "approved", snapshot.getId(), snapshot.getInputSha256(), quoteId, true, "已核对"),
                "approval-once");
        var pending = pendingDecisions.findById(requested.pending().getId()).orElseThrow();
        var command = commands.findById(requested.command().getOperationId()).orElseThrow();
        return new PendingFixture(taskId, pending, command, approvalResult(pending));
    }

    private AgentDispatcher.DispatchResult analysisResponse(
            com.caijiatai.procurement.agent.AgentCommand command) {
        return new AgentDispatcher.DispatchResult(200, Map.of(
                "status", "completed",
                "result", Map.ofEntries(
                        Map.entry("run_id", RUN_ID),
                        Map.entry("input_sha256", command.getPayload().get("input_sha256")),
                        Map.entry("structured_result", Map.of(
                                "summary", "恢复后的确定性分析输入已核对",
                                "risk_flags", List.of())),
                        Map.entry("sources", List.of()),
                        Map.entry("provider", "procurement_fake"),
                        Map.entry("model", "deterministic"),
                        Map.entry("prompt_version", "quote-analysis-v1"),
                        Map.entry("parser_version", "packaging-quote-v3"))));
    }

    private ReviewFixture pendingReview(boolean allExcluded) {
        var taskId = preparedAnalyzableTask();
        if (allExcluded) {
            taskService.correctRequirement(taskId, requirement("10000", 1));
        }
        var accepted = taskService.analyze(taskId, "review-analysis-once");
        var command = commands.findById(accepted.operationId()).orElseThrow();
        var client = mock(AgentDispatcher.class);
        when(client.dispatch(any())).thenReturn(analysisResponse(command));
        transactions.executeWithoutResult(ignored -> new AgentOutboxWorker(
                commands, tasks, client, resultApplication, aiTaskService).dispatch());
        var review = reviewRecords.findByBusinessIdOrderByCreatedAtAsc(taskId).getFirst();
        return new ReviewFixture(taskId, review);
    }

    private void completeReviewDecision(String reviewId) {
        var review = reviewRecords.findById(reviewId).orElseThrow();
        var pending = pendingDecisions.findById(review.getPendingDecisionId()).orElseThrow();
        var client = mock(AgentDispatcher.class);
        when(client.dispatch(any())).thenReturn(new AgentDispatcher.DispatchResult(
                200, Map.of("status", "completed", "result", approvalResult(pending))));
        transactions.executeWithoutResult(ignored -> new AgentOutboxWorker(
                commands, tasks, client, resultApplication, aiTaskService).dispatch());
    }

    private Map<String, Object> approvalResult(PendingDecision pending) {
        var binding = new LinkedHashMap<String, Object>();
        binding.put("pending_decision_id", pending.getId());
        binding.put("run_id", pending.getRunId());
        binding.put("tool_name", "procurement_approve_supplier");
        binding.put("task_version", pending.getTaskVersion());
        binding.put("snapshot_id", pending.getSnapshotId());
        binding.put("input_sha256", pending.getInputSha256());
        binding.put("business_decision", pending.getDecision());
        binding.put("quote_id", pending.getQuoteId());
        binding.put("note_hash", pending.getNoteHash());
        var approval = new LinkedHashMap<String, Object>(binding);
        approval.put("id", "c".repeat(32));
        approval.put("decision", "formal_java_confirmation");
        approval.put("confirmation_source", "java_control_plane");
        approval.put("arguments_sha256", CanonicalJson.sha256(binding));
        approval.put("created_at", Instant.now().toString());
        return Map.of("approval", approval);
    }

    private String preparedAnalyzableTask() {
        return transactions.execute(ignored -> {
            var task = newTask();
            task.bindAgent(SESSION_ID, RUN_ID);
            task.setStatus(TaskStatus.READY);
            task = tasks.saveAndFlush(task);
            addQuote(task.getId(), "Alpha Packaging", "520", "alpha");
            addQuote(task.getId(), "Beta Packaging", "600", "beta");
            return task.getId();
        });
    }

    private void addQuote(String taskId, String supplier, String unitPrice, String source) {
        addQuote(taskId, supplier, unitPrice, source, "CNY");
    }

    private void addQuote(
            String taskId, String supplier, String unitPrice, String source, String currency) {
        var bytes = source.getBytes(StandardCharsets.UTF_8);
        var artifact = artifactStore.store(
                "procurement_original",
                taskId,
                source + ".xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                new ByteArrayInputStream(bytes),
                Map.of("test", true));
        var values = new LinkedHashMap<String, Object>();
        values.put("supplier_name", supplier);
        values.put("item_description", "PE mailer 250x350mm 60um");
        values.put("material", "PE");
        values.put("color", "white");
        values.put("print_colors", 1);
        values.put("currency", currency);
        values.put("unit_price", unitPrice);
        values.put("price_basis", 1000);
        values.put("tax_rate", "0.13");
        values.put("tax_included", true);
        values.put("shipping_fee", "0");
        values.put("shipping_included", true);
        values.put("moq", 1000);
        values.put("lead_time_days", 7);
        values.put("supports_invoice", true);
        values.put("width_mm", "250");
        values.put("length_mm", "350");
        values.put("thickness_um", "60");
        values.put("payment_terms", "Net 30 days");
        values.put("valid_until", "2099-12-31");
        var fields = new LinkedHashMap<String, Object>();
        values.forEach((name, value) -> fields.put(
                name, Map.of("value", value, "confidence", 1, "status", "accepted")));
        quotes.save(ProcurementQuote.create(
                taskId,
                artifact.getId(),
                supplier,
                artifact.getFilename(),
                "xlsx",
                artifact.getSha256(),
                Map.of("fields", fields, "review_fields", List.of()),
                "ready",
                "integration-test",
                BigDecimal.ZERO));
    }

    private ProcurementTask newTask() {
        return ProcurementTask.structured(
                1,
                "测试采购",
                "ecommerce_packaging",
                "快递袋",
                new BigDecimal("10000"),
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
                        "thickness_tolerance_um", "3"));
    }

    private Map<String, Object> v2TapeSpecifications() {
        var specs = new LinkedHashMap<String, Object>();
        specs.put("width", dynamicNumber("宽度", "48", "mm"));
        specs.put("length", dynamicNumber("长度", "100", "m"));
        specs.put("thickness", dynamicNumber("厚度", "50", "µm"));
        specs.put("material", dynamicText("材质", "BOPP"));
        specs.put("color", dynamicText("颜色", "透明"));
        specs.put("print_colors", dynamicNumber("印刷色数", "0", ""));
        return specs;
    }

    private Map<String, Object> dynamicNumber(String label, String value, String unit) {
        return Map.of(
                "label", label, "type", "number", "value", value, "unit", unit,
                "match", "exact", "priority", "hard");
    }

    private Map<String, Object> dynamicText(String label, String value) {
        return Map.of(
                "label", label, "type", "text", "value", value,
                "match", "exact", "priority", "hard");
    }

    private ProcurementDtos.Requirement requirement(String quantity) {
        return requirement(quantity, 15);
    }

    private ProcurementDtos.Requirement requirement(String quantity, int maxLeadDays) {
        return new ProcurementDtos.Requirement(
                1,
                "测试采购",
                "ecommerce_packaging",
                "快递袋",
                new BigDecimal(quantity),
                "piece",
                Map.of(
                        "width_mm", "250",
                        "length_mm", "350",
                        "thickness_um", "60",
                        "material", "PE",
                        "color", "白色",
                        "print_colors", 1),
                new ProcurementDtos.Constraints(
                        "CNY",
                        Map.of("CNY", BigDecimal.ONE),
                        maxLeadDays,
                        true,
                        new BigDecimal("2"),
                        new BigDecimal("3"),
                        new BigDecimal("0.70"),
                        "华东仓",
                        null));
    }

    private void setTaskId(ProcurementTask task, String id) {
        try {
            var field = ProcurementTask.class.getDeclaredField("id");
            field.setAccessible(true);
            field.set(task, id);
        } catch (ReflectiveOperationException error) {
            throw new IllegalStateException(error);
        }
    }

    private record PendingFixture(
            String taskId,
            PendingDecision pending,
            com.caijiatai.procurement.agent.AgentCommand command,
            Map<String, Object> agentResult) {}

    private record ReviewFixture(String taskId, ReviewRecord review) {}
}
