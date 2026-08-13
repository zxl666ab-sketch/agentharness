package com.caijiatai.procurement.ai;

import com.caijiatai.procurement.agent.CanonicalJson;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

@Entity
@Table(name = "ai_result")
public class AiResult {
    @Id
    @Column(length = 32)
    private String id;
    @Column(name = "ai_task_id", nullable = false, unique = true, length = 32)
    private String aiTaskId;
    @Column(name = "business_id", nullable = false, length = 32)
    private String businessId;
    @Column(nullable = false)
    private int generation;
    @Column(name = "input_sha256", nullable = false, length = 64)
    private String inputSha256;
    @Column(name = "result_sha256", nullable = false, length = 64)
    private String resultSha256;
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "raw_result", columnDefinition = "json")
    private Map<String, Object> rawResult;
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "structured_result", nullable = false, columnDefinition = "json")
    private Map<String, Object> structuredResult = new LinkedHashMap<>();
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(nullable = false, columnDefinition = "json")
    private List<Map<String, Object>> sources = new ArrayList<>();
    @Column(length = 100)
    private String provider;
    @Column(length = 200)
    private String model;
    @Column(name = "prompt_version", nullable = false, length = 100)
    private String promptVersion;
    @Column(name = "parser_version", length = 100)
    private String parserVersion;
    @Column(nullable = false)
    private boolean stale;
    @Column(name = "stale_reason", length = 100)
    private String staleReason;
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    protected AiResult() {}

    public static AiResult create(
            String aiTaskId,
            String businessId,
            int generation,
            String inputSha256,
            Map<String, Object> rawResult,
            Map<String, Object> structuredResult,
            List<Map<String, Object>> sources,
            String provider,
            String model,
            String promptVersion,
            String parserVersion) {
        var result = new AiResult();
        result.id = UUID.randomUUID().toString().replace("-", "");
        result.aiTaskId = aiTaskId;
        result.businessId = businessId;
        result.generation = generation;
        result.inputSha256 = inputSha256;
        result.rawResult = rawResult == null ? null : new LinkedHashMap<>(rawResult);
        result.structuredResult = new LinkedHashMap<>(structuredResult);
        result.sources = new ArrayList<>();
        for (var source : sources) {
            result.sources.add(new LinkedHashMap<>(source));
        }
        result.resultSha256 = CanonicalJson.sha256(Map.of(
                "structured_result", result.structuredResult,
                "sources", result.sources));
        result.provider = provider;
        result.model = model;
        result.promptVersion = promptVersion;
        result.parserVersion = parserVersion;
        result.createdAt = Instant.now();
        return result;
    }

    public String getId() { return id; }
    public String getAiTaskId() { return aiTaskId; }
    public String getBusinessId() { return businessId; }
    public int getGeneration() { return generation; }
    public String getInputSha256() { return inputSha256; }
    public String getResultSha256() { return resultSha256; }
    public Map<String, Object> getRawResult() { return rawResult; }
    public Map<String, Object> getStructuredResult() { return structuredResult; }
    public List<Map<String, Object>> getSources() { return sources; }
    public String getProvider() { return provider; }
    public String getModel() { return model; }
    public String getPromptVersion() { return promptVersion; }
    public String getParserVersion() { return parserVersion; }
    public boolean isStale() { return stale; }
    public String getStaleReason() { return staleReason; }
    public Instant getCreatedAt() { return createdAt; }

    public void markStale(String reason) {
        stale = true;
        staleReason = reason == null ? null : reason.substring(0, Math.min(reason.length(), 100));
    }
}
