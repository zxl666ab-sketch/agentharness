package com.caijiatai.procurement.artifact;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;

@Entity
@Table(name = "procurement_attachment")
public class ProcurementAttachment {
    @Id
    @Column(length = 32)
    private String id;
    @Column(name = "task_id", nullable = false, length = 32)
    private String taskId;
    @Column(name = "artifact_id", nullable = false, length = 34)
    private String artifactId;
    @Column(nullable = false, length = 255)
    private String filename;
    @Column(nullable = false, length = 64)
    private String sha256;
    @Column(name = "content_type", nullable = false, length = 150)
    private String contentType;
    @Column(name = "size_bytes", nullable = false)
    private long sizeBytes;
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    protected ProcurementAttachment() {}

    public static ProcurementAttachment from(String taskId, BusinessArtifact artifact) {
        var attachment = new ProcurementAttachment();
        attachment.id = java.util.UUID.randomUUID().toString().replace("-", "");
        attachment.taskId = taskId;
        attachment.artifactId = artifact.getId();
        attachment.filename = artifact.getFilename();
        attachment.sha256 = artifact.getSha256();
        attachment.contentType = artifact.getContentType();
        attachment.sizeBytes = artifact.getSizeBytes();
        attachment.createdAt = Instant.now();
        return attachment;
    }

    public String getId() { return id; }
    public String getTaskId() { return taskId; }
    public String getArtifactId() { return artifactId; }
    public String getFilename() { return filename; }
    public String getSha256() { return sha256; }
    public String getContentType() { return contentType; }
    public long getSizeBytes() { return sizeBytes; }
    public Instant getCreatedAt() { return createdAt; }
}
