package com.caijiatai.procurement.comparison;

import static org.assertj.core.api.Assertions.assertThat;

import com.caijiatai.procurement.quote.ProcurementQuote;
import com.caijiatai.procurement.task.ProcurementTask;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.math.BigDecimal;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;
import org.junit.jupiter.api.Test;

class FrozenComparisonContractTest {
    private final ObjectMapper mapper = new ObjectMapper();
    private final ComparisonEngine engine = new ComparisonEngine();

    @Test
    void javaPreservesFrozenMatchingAmountsAndHardConstraints() throws Exception {
        var contract = mapper.readValue(
                Files.readString(Path.of("..", "contracts", "golden", "frozen-comparison-v3.json")),
                new TypeReference<Map<String, Object>>() {});
        var request = map(contract.get("request"));
        var inputs = list(contract.get("quote_inputs"));
        var task = ProcurementTask.structured(
                integer(request.getOrDefault("schema_version", 1)),
                text(request.getOrDefault("title", "冻结采购比价")),
                text(request.getOrDefault("category", "ecommerce_packaging")),
                text(request.get("item_name")),
                new BigDecimal(text(request.get("quantity"))),
                text(request.get("unit")),
                map(request.get("specifications")),
                map(request.get("constraints")));
        var quotes = new ArrayList<ProcurementQuote>();
        for (var input : inputs) {
            var fields = new LinkedHashMap<String, Object>();
            map(input.get("fields")).forEach((name, value) -> {
                var entry = new LinkedHashMap<String, Object>();
                entry.put("value", value);
                entry.put("confidence", 1);
                entry.put("status", "accepted");
                fields.put(name, entry);
            });
            quotes.add(ProcurementQuote.create(
                    task.getId(), "jb" + "a".repeat(32), text(input.get("supplier_name")),
                    text(input.get("filename")), sourceKind(text(input.get("filename"))),
                    text(input.get("source_sha256")),
                    Map.of("fields", fields, "review_fields", List.of()),
                    "ready", "frozen-contract", BigDecimal.ZERO));
        }

        var calculation = engine.compare(
                task, quotes, LocalDate.parse(text(contract.get("analysis_as_of"))));
        var actualBySupplier = list(calculation.result().get("quotes")).stream()
                .collect(Collectors.toMap(item -> text(item.get("supplier_name")), item -> item));
        int matching = 0;
        int amounts = 0;
        int missedHardConstraints = 0;
        int incorrectEligibleSelections = 0;
        var missed = new ArrayList<String>();
        for (var input : inputs) {
            var actual = actualBySupplier.get(text(input.get("supplier_name")));
            var actualMatch = Boolean.TRUE.equals(map(actual.get("match")).get("passed"));
            if (actualMatch == Boolean.TRUE.equals(input.get("expected_match"))) {
                matching += 1;
            }
            if (text(map(actual.get("cost")).get("landed_total_base"))
                    .equals(text(input.get("expected_landed_total_base")))) {
                amounts += 1;
            }
            var expectedExclusions = values(input.get("expected_exclusions")).stream()
                    .map(String::valueOf).collect(Collectors.toSet());
            var actualExclusions = list(actual.get("exclusion_reasons")).stream()
                    .map(item -> text(item.get("code"))).collect(Collectors.toSet());
            var caseMissed = difference(expectedExclusions, actualExclusions);
            missedHardConstraints += caseMissed.size();
            caseMissed.forEach(code -> missed.add(text(input.get("case_id")) + ":" + code));
            if (!expectedExclusions.isEmpty() && Boolean.TRUE.equals(actual.get("eligible"))) {
                incorrectEligibleSelections += 1;
            }
        }

        assertThat(inputs).hasSize(31);
        assertThat(matching).isEqualTo(31);
        assertThat(amounts).isEqualTo(31);
        assertThat(missed).as("missed hard constraints").isEmpty();
        assertThat(missedHardConstraints).isZero();
        assertThat(incorrectEligibleSelections).isZero();
    }

    private Set<String> difference(Set<String> expected, Set<String> actual) {
        return expected.stream().filter(item -> !actual.contains(item)).collect(Collectors.toSet());
    }

    private String sourceKind(String filename) {
        var dot = filename.lastIndexOf('.');
        return dot < 0 ? "unknown" : filename.substring(dot + 1).toLowerCase();
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> map(Object value) {
        return value instanceof Map<?, ?> raw ? (Map<String, Object>) raw : Map.of();
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> list(Object value) {
        return value instanceof List<?> raw ? (List<Map<String, Object>>) raw : List.of();
    }

    private List<?> values(Object value) {
        return value instanceof List<?> raw ? raw : List.of();
    }

    private String text(Object value) { return value == null ? "" : String.valueOf(value); }
    private int integer(Object value) { return Integer.parseInt(text(value)); }
}
