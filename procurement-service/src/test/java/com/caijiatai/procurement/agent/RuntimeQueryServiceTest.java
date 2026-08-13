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
                RuntimeEvent.create(12, null, runId, "run_completed", Map.of(),
                        Instant.parse("2026-08-13T00:00:02Z"))));
        when(tasks.findFirstByAnalysisRunId(runId)).thenReturn(Optional.empty());
        when(decisions.findByRunIdOrderByCreatedAtAsc(runId)).thenReturn(List.of());

        var report = new RuntimeQueryService(events, decisions, reports, tasks, artifacts).report(runId);

        assertThat(report).containsKeys(
                "schema_version", "run_id", "conclusion", "verification", "workspace_changes",
                "tools", "approvals", "artifacts", "usage", "events", "source", "evidence_sha256");
        assertThat(report.get("run_id")).isEqualTo(runId);
        @SuppressWarnings("unchecked")
        var conclusion = (Map<String, Object>) report.get("conclusion");
        assertThat(conclusion).containsEntry("status", "unverified");
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

        assertThatThrownBy(() -> new RuntimeQueryService(events, decisions, reports, tasks, artifacts)
                .report(runId)).isInstanceOf(ApiException.class)
                .satisfies(error -> assertThat(((ApiException) error).code()).isEqualTo("run_not_found"));
    }
}
