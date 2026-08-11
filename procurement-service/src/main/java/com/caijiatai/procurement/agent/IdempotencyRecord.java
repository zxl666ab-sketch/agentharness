package com.caijiatai.procurement.agent;

import jakarta.persistence.Column;
import jakarta.persistence.Embeddable;
import jakarta.persistence.EmbeddedId;
import jakarta.persistence.Entity;
import jakarta.persistence.Table;
import java.io.Serializable;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

@Entity
@Table(name = "idempotency_record")
public class IdempotencyRecord {
    @EmbeddedId
    private Key id;
    @Column(name = "payload_sha256", nullable = false, length = 64)
    private String payloadSha256;
    @Column(name = "operation_id", length = 36)
    private String operationId;
    @Column(name = "http_status")
    private Integer httpStatus;
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(columnDefinition = "json")
    private Map<String, Object> response;
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;
    @Column(name = "expires_at", nullable = false)
    private Instant expiresAt;

    protected IdempotencyRecord() {}

    public static IdempotencyRecord reserve(
            String scope, String key, String payloadSha256, String operationId) {
        var record = new IdempotencyRecord();
        record.id = new Key(scope, key);
        record.payloadSha256 = payloadSha256;
        record.operationId = operationId;
        record.createdAt = Instant.now();
        record.expiresAt = record.createdAt.plusSeconds(30L * 24 * 60 * 60);
        return record;
    }

    public Key getId() { return id; }
    public String getPayloadSha256() { return payloadSha256; }
    public String getOperationId() { return operationId; }
    public Integer getHttpStatus() { return httpStatus; }
    public Map<String, Object> getResponse() { return response; }

    public void complete(int status, Map<String, Object> response) {
        this.httpStatus = status;
        this.response = new LinkedHashMap<>(response);
    }

    @Embeddable
    public record Key(
            @Column(name = "scope", length = 80) String scope,
            @Column(name = "idempotency_key", length = 200) String idempotencyKey)
            implements Serializable {}
}
