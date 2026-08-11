package com.caijiatai.procurement.artifact;

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
@Table(name = "business_artifact")
public class BusinessArtifact {
    @Id
    @Column(length = 34)
    private String id;
    @Column(name = "owner_prefix", nullable = false, length = 32)
    private String ownerPrefix;
    @Column(nullable = false, length = 80)
    private String kind;
    @Column(name = "task_id", length = 32)
    private String taskId;
    @Column(nullable = false, length = 64)
    private String sha256;
    @Column(nullable = false, length = 300)
    private String locator;
    @Column(nullable = false, length = 255)
    private String filename;
    @Column(name = "content_type", nullable = false, length = 150)
    private String contentType;
    @Column(name = "size_bytes", nullable = false)
    private long sizeBytes;
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(nullable = false, columnDefinition = "json")
    private Map<String, Object> metadata = new LinkedHashMap<>();
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    protected BusinessArtifact() {}

    static BusinessArtifact create(
            String kind,
            String taskId,
            String sha256,
            String locator,
            String filename,
            String contentType,
            long sizeBytes,
            Map<String, Object> metadata) {
        var artifact = new BusinessArtifact();
        artifact.id = "jb" + java.util.UUID.randomUUID().toString().replace("-", "");
        artifact.ownerPrefix = "java-business";
        artifact.kind = kind;
        artifact.taskId = taskId;
        artifact.sha256 = sha256;
        artifact.locator = locator;
        artifact.filename = filename;
        artifact.contentType = contentType;
        artifact.sizeBytes = sizeBytes;
        artifact.metadata = new LinkedHashMap<>(metadata);
        artifact.createdAt = Instant.now();
        return artifact;
    }

    public String getId() { return id; }
    public String getOwnerPrefix() { return ownerPrefix; }
    public String getKind() { return kind; }
    public String getTaskId() { return taskId; }
    public String getSha256() { return sha256; }
    public String getLocator() { return locator; }
    public String getFilename() { return filename; }
    public String getContentType() { return contentType; }
    public long getSizeBytes() { return sizeBytes; }
    public Map<String, Object> getMetadata() { return metadata; }
    public Instant getCreatedAt() { return createdAt; }
}
