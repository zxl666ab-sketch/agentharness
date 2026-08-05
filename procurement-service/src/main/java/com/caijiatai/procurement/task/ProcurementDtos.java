package com.caijiatai.procurement.task;

import jakarta.validation.Valid;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import java.math.BigDecimal;
import java.util.Map;

public final class ProcurementDtos {
    private ProcurementDtos() {}

    public record Requirement(
            @Min(1) @Max(2) int schemaVersion,
            @NotBlank @Size(max = 200) String title,
            @NotBlank @Size(max = 100) String category,
            @NotBlank @Size(max = 200) String itemName,
            @NotNull @DecimalMin(value = "0", inclusive = false) BigDecimal quantity,
            @NotBlank @Size(max = 50) String unit,
            @NotNull @Size(max = 100) Map<String, Object> specifications,
            @NotNull @Valid Constraints constraints) {}

    public record Constraints(
            @NotBlank @Pattern(regexp = "[A-Za-z]{3}") String baseCurrency,
            @NotNull @Size(min = 1, max = 20) Map<String, BigDecimal> fxRates,
            @Min(1) @Max(3650) int maxLeadDays,
            boolean invoiceRequired,
            BigDecimal sizeToleranceMm,
            BigDecimal thicknessToleranceUm,
            BigDecimal maxLandedUnitCost,
            @Size(max = 300) String destination,
            String requiredDeliveryDate) {}

    public record QuoteCorrection(@NotBlank @Size(max = 100) String field, Object value) {}

    public record Resume(@NotBlank @Size(max = 20_000) String message) {}

    public record Decision(
            @NotBlank String decision,
            @NotBlank String snapshotId,
            @Pattern(regexp = "[0-9a-f]{64}") String inputSha256,
            String quoteId,
            boolean confirmed,
            @Size(max = 2_000) String note) {}

    public record Reopen(boolean copyQuotes) {}

    public record OperationAccepted(
            String operationId,
            String purchaseRequestId,
            String sessionId,
            String runId,
            String status,
            String location) {}
}
