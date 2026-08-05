package com.caijiatai.procurement.agent;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.math.BigDecimal;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.HexFormat;
import java.util.Map;
import org.junit.jupiter.api.Test;

class CanonicalJsonTest {
    private final ObjectMapper mapper = new ObjectMapper();

    @Test
    void javaBytesMatchFrozenPythonBaseline() throws Exception {
        var root = Path.of("..", "contracts", "golden");
        var value = mapper.readValue(
                Files.readString(root.resolve("canonical-json.json")),
                new TypeReference<Map<String, Object>>() {});
        var manifest = mapper.readTree(Files.readString(root.resolve("manifest.json")));
        var bytes = CanonicalJson.bytes(value);

        assertThat(bytes).hasSize(manifest.at("/canonical_json/utf8_length").asInt());
        assertThat(HexFormat.of().formatHex(bytes))
                .isEqualTo(manifest.at("/canonical_json/utf8_hex").asText());
        assertThat(HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes)))
                .isEqualTo(manifest.at("/canonical_json/sha256").asText());
    }

    @Test
    void decimalsUsePlainStringsAndNormalizeTrailingZeros() {
        assertThat(CanonicalJson.decimal(new BigDecimal("5200.00"))).isEqualTo("5200");
        assertThat(CanonicalJson.decimal(new BigDecimal("0.5200"))).isEqualTo("0.52");
        assertThat(CanonicalJson.decimal(new BigDecimal("1E+3"))).isEqualTo("1000");
        assertThat(CanonicalJson.decimal(new BigDecimal("-0.000"))).isEqualTo("0");
    }

    @Test
    void instantsUseUtcIsoStringsInCanonicalEvidence() {
        assertThat(new String(CanonicalJson.bytes(Map.of(
                "created_at", Instant.parse("2026-08-04T16:49:50.204665Z")))))
                .isEqualTo("{\"created_at\":\"2026-08-04T16:49:50.204665Z\"}");
    }
}
