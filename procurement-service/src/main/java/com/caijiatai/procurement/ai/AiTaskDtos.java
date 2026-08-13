package com.caijiatai.procurement.ai;

import jakarta.validation.constraints.Size;
import tools.jackson.databind.PropertyNamingStrategies;
import tools.jackson.databind.annotation.JsonNaming;

public final class AiTaskDtos {
    private AiTaskDtos() {}

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record CreateAiTaskRequest(
            AiTaskType taskType,
            @Size(min = 8, max = 128) String idempotencyKey) {}
}
