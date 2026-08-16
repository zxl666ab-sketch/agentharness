package com.caijiatai.procurement.quote;

import java.util.List;
import java.util.Map;

/**
 * P2-2 冲突裁决：人工修正若标记 chosen_from_conflicts，必须命中字段冲突候选值。
 * 比较按字符串规范化进行（数字 520 与字符串 "520" 视为同一候选，容忍解析类型差异；
 * null 永不命中）。
 */
public final class CorrectionConflictPolicy {

    private CorrectionConflictPolicy() {}

    public static boolean chosenValueMatchesConflicts(Map<String, Object> fieldEntry, Object value) {
        if (value == null) {
            return false;
        }
        Object raw = fieldEntry.get("conflicts");
        if (!(raw instanceof List<?> conflicts)) {
            return false;
        }
        String target = String.valueOf(value).trim();
        for (Object candidate : conflicts) {
            if (!(candidate instanceof Map<?, ?> map)) {
                continue;
            }
            Object candidateValue = map.get("value");
            if (candidateValue != null && String.valueOf(candidateValue).trim().equals(target)) {
                return true;
            }
        }
        return false;
    }
}
