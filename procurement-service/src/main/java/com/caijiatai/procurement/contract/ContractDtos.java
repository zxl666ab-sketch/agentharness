package com.caijiatai.procurement.contract;

import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;
import jakarta.validation.constraints.Size;
import java.math.BigDecimal;

/** 合同接口 DTO（P3-2）。 */
public final class ContractDtos {
    private ContractDtos() {}

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record ContractAction(
            Boolean confirmed,
            @Size(max = 2_000) String notes,
            BigDecimal newAmount /* 变更：修订后的合同金额（request_change 必填） */,
            Integer newLeadDays /* 变更：修订后的交期天数（request_change 必填） */) {}
}
