package com.caijiatai.procurement.review;

import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import java.util.Map;
import tools.jackson.databind.PropertyNamingStrategies;
import tools.jackson.databind.annotation.JsonNaming;

public final class ReviewDtos {
    private ReviewDtos() {}

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record ActionRequest(
            @NotNull ReviewAction action,
            @Min(0) long expectedVersion,
            @NotBlank @Size(max = 100) String actor,
            @Size(max = 32) String finalQuoteId,
            @Size(max = 100) Map<String, Object> revisions,
            @Size(max = 2_000) String reason) {}
}
