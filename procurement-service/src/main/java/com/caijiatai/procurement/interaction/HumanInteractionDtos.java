package com.caijiatai.procurement.interaction;

import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import tools.jackson.databind.PropertyNamingStrategies;
import tools.jackson.databind.annotation.JsonNaming;

public final class HumanInteractionDtos {
    private HumanInteractionDtos() {}

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record Answer(
            @NotNull Object answer,
            @Size(max = 2_000) String note,
            @Size(max = 20) List<String> artifactIds) {}

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record Cancel(@Size(max = 500) String reason) {}

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record ArtifactView(
            String artifactId, String filename, String contentType, long sizeBytes, String sha256) {}

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record View(
            String id, String taskId, String runId, String checkpointId, int generation, String kind,
            String question, String reason, String businessStep, List<String> relatedFields,
            List<String> relatedArtifactIds, Map<String, Object> answerSchema, String status,
            Object answer, String answerNote, List<String> answerArtifactIds, String answeredBy,
            Instant answeredAt, Instant appliedAt, Instant expiresAt, String cancelReason,
            String operationId, Instant createdAt, Instant updatedAt) {}
}
