package com.caijiatai.procurement.comparison;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

@Entity
@Table(name = "comparison_snapshot")
public class ComparisonSnapshot {
    @Id
    @Column(length = 32)
    private String id;
    @Column(name = "task_id", nullable = false, length = 32)
    private String taskId;
    @Column(name = "run_id", nullable = false, length = 32)
    private String runId;
    @Column(name = "snapshot_version", nullable = false)
    private int snapshotVersion;
    @Column(name = "task_version", nullable = false)
    private long taskVersion;
    @Column(name = "input_sha256", nullable = false, length = 64)
    private String inputSha256;
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(nullable = false, columnDefinition = "json")
    private Map<String, Object> result = new LinkedHashMap<>();
    @Column(name = "artifact_id", nullable = false, length = 34)
    private String artifactId;
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    protected ComparisonSnapshot() {}

    public static ComparisonSnapshot create(
            String taskId,
            String runId,
            int version,
            long taskVersion,
            String inputSha256,
            Map<String, Object> result,
            String artifactId) {
        var snapshot = new ComparisonSnapshot();
        snapshot.id = java.util.UUID.randomUUID().toString().replace("-", "");
        snapshot.taskId = taskId;
        snapshot.runId = runId;
        snapshot.snapshotVersion = version;
        snapshot.taskVersion = taskVersion;
        snapshot.inputSha256 = inputSha256;
        snapshot.result = new LinkedHashMap<>(result);
        snapshot.artifactId = artifactId;
        snapshot.createdAt = Instant.now();
        return snapshot;
    }

    public String getId() { return id; }
    public String getTaskId() { return taskId; }
    public String getRunId() { return runId; }
    public int getSnapshotVersion() { return snapshotVersion; }
    public long getTaskVersion() { return taskVersion; }
    public String getInputSha256() { return inputSha256; }
    public Map<String, Object> getResult() { return result; }
    public String getArtifactId() { return artifactId; }
    public Instant getCreatedAt() { return createdAt; }
}
