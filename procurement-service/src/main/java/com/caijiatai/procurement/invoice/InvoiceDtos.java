package com.caijiatai.procurement.invoice;

import jakarta.validation.constraints.Size;
import java.math.BigDecimal;

/** 发票接口 DTO（P3-1）。 */
public final class InvoiceDtos {
    private InvoiceDtos() {}

    @com.fasterxml.jackson.databind.annotation.JsonNaming(
            com.fasterxml.jackson.databind.PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record InvoiceAction(
            BigDecimal quantity,
            BigDecimal unitPrice,
            BigDecimal amountExcludingTax,
            BigDecimal taxAmount,
            BigDecimal totalAmount,
            BigDecimal taxRate,
            Boolean confirmed,
            @Size(max = 2_000) String notes) {}
}
