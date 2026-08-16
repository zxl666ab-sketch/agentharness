package com.caijiatai.procurement.quote;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

class CorrectionConflictPolicyTest {

    private static Map<String, Object> candidate(String locator, Object value) {
        return Map.of(
                "value", value,
                "confidence", 0.6,
                "source", Map.of("document_kind", "xlsx", "locator", locator, "method", "key_value_cell"));
    }

    @Test
    void matchesAChosenCandidateValue() {
        var entry = Map.<String, Object>of(
                "conflicts", List.of(candidate("B4", "520"), candidate("C2", "580")));
        assertTrue(CorrectionConflictPolicy.chosenValueMatchesConflicts(entry, "520"));
        // 数字与字符串视为同一候选（解析类型差异容忍）
        assertTrue(CorrectionConflictPolicy.chosenValueMatchesConflicts(entry, 520));
        assertTrue(CorrectionConflictPolicy.chosenValueMatchesConflicts(entry, "580"));
    }

    @Test
    void rejectsValuesOutsideConflicts() {
        var entry = Map.<String, Object>of(
                "conflicts", List.of(candidate("B4", "520")));
        assertFalse(CorrectionConflictPolicy.chosenValueMatchesConflicts(entry, "999"));
        assertFalse(CorrectionConflictPolicy.chosenValueMatchesConflicts(entry, null));
    }

    @Test
    void rejectsWhenNoConflictsExist() {
        var entry = Map.<String, Object>of("value", "520");
        assertFalse(CorrectionConflictPolicy.chosenValueMatchesConflicts(entry, "520"));
        assertFalse(CorrectionConflictPolicy.chosenValueMatchesConflicts(Map.of(), "520"));
    }
}
