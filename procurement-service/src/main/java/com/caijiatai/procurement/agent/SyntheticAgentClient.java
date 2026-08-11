package com.caijiatai.procurement.agent;

import com.caijiatai.procurement.config.AppProperties;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Primary;
import org.springframework.stereotype.Component;

/**
 * Demo-mode agent dispatcher: completes commands deterministically on the Java side
 * so the procurement closed loop runs without the Python agent (decision gate 1).
 * Synthetic results never carry business truth beyond what the Java engine computes.
 */
@Component
@Primary
@ConditionalOnProperty(prefix = "app.agent", name = "mode", havingValue = "demo")
public final class SyntheticAgentClient extends AgentClient {

    public SyntheticAgentClient(AppProperties properties) {
        super(null, properties);
    }

    @Override
    public DispatchResult dispatch(AgentCommand command) {
        var result = switch (command.getOperationType()) {
            case "create_structured", "start_conversation" -> Map.of(
                    "session_id", sha256hex(command.getOperationId() + ":session"),
                    "run_id", sha256hex(command.getOperationId() + ":run"));
            case "analyze" -> Map.of(
                    "run_id", sha256hex(command.getOperationId() + ":run"));
            case "approve_decision" -> approval(command);
            case "import_quote", "reopen_task", "resume_run" -> Map.of(
                    "run_id", sha256hex(command.getOperationId() + ":run"));
            default -> Map.of();
        };
        return new DispatchResult(200, Map.of("status", "completed", "result", result));
    }

    private Map<String, Object> approval(AgentCommand command) {
        var payload = command.getPayload();
        var binding = new LinkedHashMap<String, Object>();
        binding.put("pending_decision_id", text(payload.get("pending_decision_id")));
        binding.put("run_id", text(payload.get("run_id")));
        binding.put("tool_name", "procurement_approve_supplier");
        binding.put("task_version", String.valueOf(payload.getOrDefault("task_version", "")));
        binding.put("snapshot_id", text(payload.get("snapshot_id")));
        binding.put("input_sha256", text(payload.get("input_sha256")));
        binding.put("business_decision", text(payload.get("business_decision")));
        binding.put("quote_id", text(payload.get("quote_id")));
        binding.put("note_hash", text(payload.get("note_hash")));
        var approval = new LinkedHashMap<String, Object>(binding);
        approval.put("id", sha256hex(command.getOperationId() + ":approval"));
        approval.put("decision", "formal_java_confirmation");
        approval.put("confirmation_source", "java_control_plane");
        approval.put("arguments_sha256", CanonicalJson.sha256(binding));
        approval.put("created_at", Instant.now().toString());
        return Map.of("approval", approval);
    }

    private String text(Object value) {
        return value == null ? "" : String.valueOf(value);
    }

    private String sha256hex(String value) {
        try {
            var digest = MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest).substring(0, 32);
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException(error);
        }
    }
}
