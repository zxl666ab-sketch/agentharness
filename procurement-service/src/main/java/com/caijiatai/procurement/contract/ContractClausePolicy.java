package com.caijiatai.procurement.contract;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * P3-2 必填条款校验：金额条款与交期条款必须存在（按条款标题关键字）。
 */
public final class ContractClausePolicy {

    private ContractClausePolicy() {}

    public static Map<String, Object> validate(List<Map<String, Object>> clauses) {
        boolean hasAmount = false;
        boolean hasLeadDays = false;
        for (Map<String, Object> clause : clauses) {
            String title = String.valueOf(clause.getOrDefault("title", ""));
            if (title.contains("金额")) {
                hasAmount = true;
            }
            if (title.contains("交期") || title.contains("交货期") || title.contains("交付期")) {
                hasLeadDays = true;
            }
        }
        var value = new LinkedHashMap<String, Object>();
        value.put("amount_clause_present", hasAmount);
        value.put("lead_days_clause_present", hasLeadDays);
        value.put("valid", hasAmount && hasLeadDays);
        return value;
    }
}
