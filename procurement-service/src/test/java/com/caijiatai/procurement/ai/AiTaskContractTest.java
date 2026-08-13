package com.caijiatai.procurement.ai;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;
import java.util.stream.Collectors;
import org.junit.jupiter.api.Test;

class AiTaskContractTest {
    private final ObjectMapper mapper = new ObjectMapper();
    private final Path contract = Path.of("..", "contracts", "procurement-workbench.schema.json");

    @Test
    void javaEnumsAndTransitionsMatchSharedContract() throws Exception {
        JsonNode schema = mapper.readTree(Files.readString(contract));

        assertThat(enumValues(schema, "AiTaskStatus"))
                .isEqualTo(names(AiTaskStatus.values()));
        assertThat(enumValues(schema, "AiTaskType"))
                .isEqualTo(names(AiTaskType.values()));
        assertThat(enumValues(schema, "AiTaskStep"))
                .isEqualTo(names(AiTaskStep.values()));

        JsonNode transitions = schema.path("x-ai-status-transitions");
        for (AiTaskStatus status : AiTaskStatus.values()) {
            Set<String> expected = new HashSet<>();
            transitions.path(status.name()).forEach(value -> expected.add(value.asText()));
            assertThat(status.allowedTargets().stream().map(Enum::name).collect(Collectors.toSet()))
                    .isEqualTo(expected);
        }
    }

    @Test
    void failureAndStaleExamplesUseFrozenViewShape() throws Exception {
        JsonNode failed = readExample("ai-task-failed.json");
        JsonNode stale = readExample("ai-task-stale.json");

        assertThat(failed.path("status").asText()).isEqualTo(AiTaskStatus.FAILED.name());
        assertThat(failed.path("error_code").asText()).isNotBlank();
        assertThat(failed.path("retryable").asBoolean()).isTrue();
        assertThat(stale.path("status").asText()).isEqualTo(AiTaskStatus.SUCCEEDED.name());
        assertThat(stale.path("stale").asBoolean()).isTrue();
        assertThat(stale.path("stale_reason").asText()).isNotBlank();
    }

    private JsonNode readExample(String filename) throws Exception {
        return mapper.readTree(Files.readString(
                Path.of("..", "contracts", "examples", filename)));
    }

    private Set<String> enumValues(JsonNode schema, String name) {
        Set<String> result = new HashSet<>();
        schema.path("$defs").path(name).path("enum").forEach(value -> result.add(value.asText()));
        return result;
    }

    private Set<String> names(Enum<?>[] values) {
        return Arrays.stream(values).map(Enum::name).collect(Collectors.toSet());
    }
}
