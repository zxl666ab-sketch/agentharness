package com.caijiatai.procurement.report;

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
@Table(name = "runtime_report_projection")
public class RuntimeReportProjection {
    @Id
    @Column(length = 32)
    private String id;
    @Column(name = "task_id", nullable = false, length = 32)
    private String taskId;
    @Column(name = "run_id", nullable = false, length = 32)
    private String runId;
    @Column(name = "evidence_sha256", nullable = false, length = 64)
    private String evidenceSha256;
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(nullable = false, columnDefinition = "json")
    private Map<String, Object> report = new LinkedHashMap<>();
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    protected RuntimeReportProjection() {}

    public static RuntimeReportProjection create(
            String taskId, String runId, String evidenceSha256, Map<String, Object> report) {
        var projection = new RuntimeReportProjection();
        projection.id = java.util.UUID.randomUUID().toString().replace("-", "");
        projection.taskId = taskId;
        projection.runId = runId;
        projection.evidenceSha256 = evidenceSha256;
        projection.report = new LinkedHashMap<>(report);
        projection.createdAt = Instant.now();
        return projection;
    }

    public String getEvidenceSha256() { return evidenceSha256; }
    public Map<String, Object> getReport() { return report; }
}
