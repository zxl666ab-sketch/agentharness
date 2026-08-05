package com.caijiatai.procurement.comparison;

import com.caijiatai.procurement.agent.CanonicalJson;
import com.caijiatai.procurement.api.ApiException;
import com.caijiatai.procurement.artifact.ArtifactStore;
import com.caijiatai.procurement.quote.ProcurementQuoteRepository;
import com.caijiatai.procurement.report.AuditEvent;
import com.caijiatai.procurement.report.AuditEventRepository;
import com.caijiatai.procurement.task.ProcurementTask;
import java.io.ByteArrayInputStream;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

@Service
public final class ComparisonService {
    private final ProcurementQuoteRepository quotes;
    private final ComparisonSnapshotRepository snapshots;
    private final ComparisonEngine engine;
    private final ArtifactStore artifacts;
    private final AuditEventRepository audit;

    public ComparisonService(
            ProcurementQuoteRepository quotes,
            ComparisonSnapshotRepository snapshots,
            ComparisonEngine engine,
            ArtifactStore artifacts,
            AuditEventRepository audit) {
        this.quotes = quotes;
        this.snapshots = snapshots;
        this.engine = engine;
        this.artifacts = artifacts;
        this.audit = audit;
    }

    public ComparisonSnapshot analyze(ProcurementTask task, String runId) {
        var taskQuotes = quotes.findByTaskIdOrderByCreatedAtAsc(task.getId());
        if (taskQuotes.stream().anyMatch(item -> !item.reviewFields().isEmpty())) {
            throw new ApiException(HttpStatus.CONFLICT, "quote_review_required", "报价字段尚未全部复核");
        }
        var calculation = engine.compare(task, taskQuotes);
        var artifact = artifacts.store(
                "comparison_snapshot",
                task.getId(),
                task.getReference() + "-comparison.json",
                "application/json",
                new ByteArrayInputStream(CanonicalJson.bytes(calculation.result())),
                Map.of("run_id", runId, "input_sha256", calculation.inputSha256()));
        var version = snapshots.findFirstByTaskIdOrderBySnapshotVersionDesc(task.getId())
                .map(item -> item.getSnapshotVersion() + 1)
                .orElse(1);
        var snapshot = snapshots.save(ComparisonSnapshot.create(
                task.getId(), runId, version, task.getVersion(), calculation.inputSha256(),
                calculation.result(), artifact.getId()));
        task.useSnapshot(snapshot.getId());
        audit.save(AuditEvent.create(
                task.getId(), null, runId, "comparison_snapshot_created", "agent",
                Map.of(
                        "snapshot_id", snapshot.getId(),
                        "input_sha256", snapshot.getInputSha256(),
                        "artifact_id", artifact.getId())));
        return snapshot;
    }
}
