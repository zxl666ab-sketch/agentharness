package com.caijiatai.procurement.agent;

import com.caijiatai.procurement.config.AppProperties;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.client.HttpClientErrorException;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

@Component
public class AgentClient {
    private final RestClient client;
    private final String token;

    public AgentClient(RestClient agentRestClient, AppProperties properties) {
        this.client = agentRestClient;
        this.token = properties.agentInternalToken();
    }

    @SuppressWarnings("unchecked")
    public DispatchResult dispatch(AgentCommand command) {
        var body = new LinkedHashMap<String, Object>();
        body.put("operation_id", command.getOperationId());
        body.put("operation_type", command.getOperationType());
        body.put("aggregate_id", command.getAggregateId());
        body.put("generation", command.getGeneration());
        body.put("expected_task_version", command.getExpectedTaskVersion());
        body.put("payload_sha256", command.getPayloadSha256());
        body.put("payload", command.getPayload());
        try {
            var response = client.post()
                    .uri("/internal/v1/commands")
                    .header("X-Agent-Internal-Token", token)
                    .header("X-Operation-Id", command.getOperationId())
                    .header("X-Payload-SHA256", command.getPayloadSha256())
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(body)
                    .retrieve()
                    .toEntity(Map.class);
            return new DispatchResult(response.getStatusCode().value(),
                    response.getBody() == null ? Map.of() : (Map<String, Object>) response.getBody());
        } catch (HttpClientErrorException.Conflict error) {
            return new DispatchResult(409, Map.of("error", "operation_payload_conflict"));
        } catch (RestClientException error) {
            throw new AgentUnavailableException(error.getMessage(), error);
        }
    }

    public record DispatchResult(int status, Map<String, Object> body) {}

    public static final class AgentUnavailableException extends RuntimeException {
        AgentUnavailableException(String message, Throwable cause) {
            super(message, cause);
        }
    }
}
