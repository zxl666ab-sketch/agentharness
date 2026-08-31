package com.caijiatai.procurement.agent;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import com.caijiatai.procurement.api.ApiException;
import com.caijiatai.procurement.approval.ProcurementDecisionRepository;
import com.caijiatai.procurement.artifact.BusinessArtifactRepository;
import com.caijiatai.procurement.report.RuntimeReportProjectionRepository;
import com.caijiatai.procurement.task.ProcurementTaskRepository;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import org.junit.jupiter.api.Test;
import org.springframework.data.domain.Pageable;

class RuntimeQueryServiceTest {
    @Test
    void agentAvailabilityOrdersHeartbeatsByOccurredAtNotGlobalSeq() {
        // LIVE-1: a stale heartbeat kept at the highest global_seq (topic pruning + seq
        // re-seeding) must not decide availability; the newest-by-time row must.
        var events = mock(RuntimeEventRepository.class);
        var service = new RuntimeQueryService(
                events,
                mock(ProcurementDecisionRepository.class),
                mock(RuntimeReportProjectionRepository.class),
                mock(ProcurementTaskRepository.class),
                mock(BusinessArtifactRepository.class),
                new CostService(events, new ModelPricingService("")));
        var fresh = RuntimeEvent.create(7L, null, null, "heartbeat.ping", Map.of(),
                Instant.now().minusSeconds(2));
        when(events.findFirstByTypeOrderByOccurredAtDesc("heartbeat.ping")).thenReturn(Optional.of(fresh));
        assertThat(service.agentAvailable()).isTrue();

        var staleHighSeq = RuntimeEvent.create(86565L, null, null, "heartbeat.ping", Map.of(),
                Instant.now().minusSeconds(3600));
        when(events.findFirstByTypeOrderByOccurredAtDesc("heartbeat.ping")).thenReturn(Optional.of(staleHighSeq));
        assertThat(service.agentAvailable()).isFalse();

        when(events.findFirstByTypeOrderByOccurredAtDesc("heartbeat.ping")).thenReturn(Optional.empty());
        assertThat(service.agentAvailable()).isFalse();
    }

    @Test
    void projectsRunReportFromJavaEventsWithStableFrontendContract() {
        var events = mock(RuntimeEventRepository.class);
        var decisions = mock(ProcurementDecisionRepository.class);
        var reports = mock(RuntimeReportProjectionRepository.class);
        var tasks = mock(ProcurementTaskRepository.class);
        var artifacts = mock(BusinessArtifactRepository.class);
        var runId = "a".repeat(32);
        when(reports.findFirstByRunIdOrderByCreatedAtDesc(runId)).thenReturn(Optional.empty());
        when(events.findByRunId(any(), any(Pageable.class))).thenReturn(List.of(
                RuntimeEvent.create(10, null, runId, "run_started", Map.of(),
                        Instant.parse("2026-08-13T00:00:00Z")),
                RuntimeEvent.create(11, null, runId, "ai_task.step", Map.of(
                        "step", "QUOTE_PARSE", "summary", "解析报价"),
                        Instant.parse("2026-08-13T00:00:01Z")),
                RuntimeEvent.create(12, null, runId, "run_completed", Map.of(
                        "usage", Map.of(
                                "input_tokens", 100,
                                "output_tokens", 23,
                                "total_tokens", 123,
                                "model_turns", 2)),
                        Instant.parse("2026-08-13T00:00:02Z"))));
        when(tasks.findFirstByAnalysisRunId(runId)).thenReturn(Optional.empty());
        when(decisions.findByRunIdOrderByCreatedAtAsc(runId)).thenReturn(List.of());

        var report = new RuntimeQueryService(events, decisions, reports, tasks, artifacts,
                new CostService(events, new ModelPricingService(""))).report(runId);

        assertThat(report).containsKeys(
                "schema_version", "run_id", "conclusion", "verification", "workspace_changes",
                "tools", "approvals", "artifacts", "usage", "events", "source", "evidence_sha256");
        assertThat(report.get("run_id")).isEqualTo(runId);
        @SuppressWarnings("unchecked")
        var conclusion = (Map<String, Object>) report.get("conclusion");
        assertThat(conclusion).containsEntry("status", "unverified");
        @SuppressWarnings("unchecked")
        var usage = (Map<String, Object>) report.get("usage");
        assertThat(usage).containsEntry("total_tokens", 123).containsEntry("model_turns", 2);
        assertThat((List<?>) report.get("events")).hasSize(3);
        assertThat(String.valueOf(report.get("evidence_sha256"))).matches("[0-9a-f]{64}");
    }

    @Test
    void rejectsUnknownRunInsteadOfReturningAnEmptyReport() {
        var events = mock(RuntimeEventRepository.class);
        var decisions = mock(ProcurementDecisionRepository.class);
        var reports = mock(RuntimeReportProjectionRepository.class);
        var tasks = mock(ProcurementTaskRepository.class);
        var artifacts = mock(BusinessArtifactRepository.class);
        var runId = "b".repeat(32);
        when(reports.findFirstByRunIdOrderByCreatedAtDesc(runId)).thenReturn(Optional.empty());
        when(events.findByRunId(any(), any(Pageable.class))).thenReturn(List.of());
        when(tasks.findFirstByAnalysisRunId(runId)).thenReturn(Optional.empty());

        assertThatThrownBy(() -> new RuntimeQueryService(events, decisions, reports, tasks, artifacts,
                new CostService(events, new ModelPricingService(""))).report(runId))
                .isInstanceOf(ApiException.class)
                .satisfies(error -> assertThat(((ApiException) error).code()).isEqualTo("run_not_found"));
    }

    @Test
    void projectsLatestHumanGateInsteadOfFlatteningRunStatusToRunning() {
        var events = mock(RuntimeEventRepository.class);
        var decisions = mock(ProcurementDecisionRepository.class);
        var reports = mock(RuntimeReportProjectionRepository.class);
        var tasks = mock(ProcurementTaskRepository.class);
        var artifacts = mock(BusinessArtifactRepository.class);
        var runId = "c".repeat(32);
        var started = RuntimeEvent.create(10, null, runId, "run_started", Map.of(),
                Instant.parse("2026-08-13T00:00:00Z"));
        var modelTurn = RuntimeEvent.create(249, null, runId, "model_turn_end", Map.of(
                "provider", "procurement_openai",
                "model", "deepseek-v4-flash",
                "usage", Map.of("input_tokens", 80, "output_tokens", 20)),
                Instant.parse("2026-08-13T00:04:59Z"));
        var paused = RuntimeEvent.create(250, null, runId, "run_status",
                Map.of("status", "waiting_approval"),
                Instant.parse("2026-08-13T00:05:00Z"));
        when(events.findByRunId(any(), any(Pageable.class))).thenReturn(List.of(paused, modelTurn));
        when(events.findFirstByRunIdOrderByGlobalSeqAsc(runId)).thenReturn(Optional.of(started));
        when(events.countByRunId(runId)).thenReturn(249L);
        when(tasks.findFirstByAnalysisRunId(runId)).thenReturn(Optional.empty());

        var run = new RuntimeQueryService(events, decisions, reports, tasks, artifacts,
                new CostService(events, new ModelPricingService(""))).run(runId);

        assertThat(run).containsEntry("status", "waiting_approval");
        assertThat(run).containsEntry("event_count", 249L);
        assertThat(run).containsEntry("started_at", started.getOccurredAt());
        assertThat(run).containsEntry("updated_at", paused.getOccurredAt());
        assertThat(run).containsEntry("provider", "procurement_openai");
        assertThat(run).containsEntry("model", "deepseek-v4-flash");
        assertThat(run.get("usage_json")).isEqualTo(
                "{\"cached_input_tokens\":0,\"cost_status\":\"unknown\",\"estimated_cost_usd\":null,"
                        + "\"input_tokens\":80,\"model_turns\":1,\"output_tokens\":20,\"total_tokens\":100}");
    }
}
