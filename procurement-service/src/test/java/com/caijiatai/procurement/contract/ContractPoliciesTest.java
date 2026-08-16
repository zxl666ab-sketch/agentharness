package com.caijiatai.procurement.contract;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

class ContractPoliciesTest {

    @Test
    void consistencyMatchesWhenDraftUsesInjectedValues() {
        var draft = "采购合同\n一、合同金额为人民币 7500.00 元（价税合计）。\n"
                + "二、乙方应于合同生效后 15 天内交货。\n";
        var result = ContractConsistencyPolicy.check(draft, new BigDecimal("7500"), 15);
        assertTrue(Boolean.TRUE.equals(result.get("amount_matches")));
        assertTrue(Boolean.TRUE.equals(result.get("lead_days_matches")));
        assertTrue(Boolean.TRUE.equals(result.get("consistent")));
    }

    @Test
    void consistencyFlagsMismatchedAmountAndLeadDays() {
        var draft = "一、合同金额为人民币 7800 元。\n二、交期 20 天。\n";
        var result = ContractConsistencyPolicy.check(draft, new BigDecimal("7500"), 15);
        assertFalse(Boolean.TRUE.equals(result.get("amount_matches")));
        assertFalse(Boolean.TRUE.equals(result.get("lead_days_matches")));
        assertFalse(Boolean.TRUE.equals(result.get("consistent")));
        assertEquals("7800", result.get("amount_in_text"));
        assertEquals("20", result.get("lead_days_in_text"));
    }

    @Test
    void amountExtractionHandlesYuanAndCurrencySymbols() {
        assertEquals("7500", ContractConsistencyPolicy.extractAmount("总金额：¥7500.00"));
        assertEquals("7500", ContractConsistencyPolicy.extractAmount("合同金额 7500 元"));
        assertEquals("5200", ContractConsistencyPolicy.extractAmount("价税合计 5200.00"));
    }

    @Test
    void leadDaysExtractionHandlesCommonPhrasings() {
        assertEquals("15", ContractConsistencyPolicy.extractLeadDays("交期为 15 天"));
        assertEquals("10", ContractConsistencyPolicy.extractLeadDays("交货期：10 个工作日"));
        assertEquals("20", ContractConsistencyPolicy.extractLeadDays("20 天内交货"));
    }

    @Test
    void clauseValidationRequiresAmountAndLeadDaysClauses() {
        var valid = ContractClausePolicy.validate(List.of(
                Map.of("title", "金额条款"),
                Map.of("title", "交期条款"),
                Map.of("title", "质量标准条款")));
        assertTrue(Boolean.TRUE.equals(valid.get("valid")));

        var missing = ContractClausePolicy.validate(List.of(
                Map.of("title", "质量标准条款"),
                Map.of("title", "付款条款")));
        assertFalse(Boolean.TRUE.equals(missing.get("valid")));
        assertFalse(Boolean.TRUE.equals(missing.get("amount_clause_present")));
        assertFalse(Boolean.TRUE.equals(missing.get("lead_days_clause_present")));
    }
}
