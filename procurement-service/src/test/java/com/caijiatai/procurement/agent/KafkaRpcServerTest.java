package com.caijiatai.procurement.agent;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.caijiatai.procurement.approval.PendingDecision;
import com.caijiatai.procurement.approval.PendingDecisionRepository;
import com.caijiatai.procurement.artifact.ArtifactStore;
import com.caijiatai.procurement.artifact.BusinessArtifactRepository;
import com.caijiatai.procurement.config.AppProperties;
import com.caijiatai.procurement.quote.ProcurementQuoteRepository;
import com.caijiatai.procurement.task.ProcurementTask;
import com.caijiatai.procurement.task.ProcurementTaskRepository;
import com.caijiatai.procurement.task.TaskViewMapper;
import java.math.BigDecimal;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.kafka.core.KafkaTemplate;

class KafkaRpcServerTest {
    @Test
    void taskContextIncludesAuthoritativePendingDecisionBinding() {
        var kafka = mock(KafkaTemplate.class);
        var properties = mock(AppProperties.class);
        when(properties.internalHmacKey()).thenReturn("test-hmac-key-with-at-least-32-bytes");
        var tasks = mock(ProcurementTaskRepository.class);
        var quotes = mock(ProcurementQuoteRepository.class);
        var pendingDecisions = mock(PendingDecisionRepository.class);
        var task = ProcurementTask.structured(
                1, "胶带采购", "ecommerce_packaging", "胶带", BigDecimal.TEN,
                "piece", Map.of(), Map.of());
        task.bindAgent("a".repeat(32), "b".repeat(32));
        var pending = PendingDecision.create(
                "c".repeat(32), "12345678-1234-1234-1234-123456789abc",
                task.getId(), task.getAnalysisRunId(), task.getVersion(), "d".repeat(32),
                "e".repeat(64), "approved", "f".repeat(32), "0".repeat(64));
        when(tasks.findById(task.getId())).thenReturn(Optional.of(task));
        when(quotes.findByTaskIdOrderByCreatedAtAsc(task.getId())).thenReturn(List.of());
        when(pendingDecisions.findByTaskIdAndStatusIn(
                task.getId(), List.of("pending", "approved", "stale")))
                .thenReturn(List.of(pending));
        var server = new KafkaRpcServer(
                kafka, properties, tasks, quotes, mock(BusinessArtifactRepository.class),
                mock(ArtifactStore.class), mock(RuntimeEventRepository.class), new TaskViewMapper(),
                mock(ReferencePriceService.class), pendingDecisions);
        var payload = Map.<String, Object>of("task_id", task.getId());
        var request = new java.util.LinkedHashMap<String, Object>();
        request.put("correlation_id", "corr-1");
        request.put("kind", "get_task_context");
        request.put("payload", payload);
        request.put("request_sha256", CanonicalJson.sha256(payload));
        request.put("signature", MessageCodec.signEnvelope(properties.internalHmacKey(), request));

        server.onRequest(new ConsumerRecord<>(
                KafkaRpcServer.REQUESTS_TOPIC, 0, 0, "corr-1", CanonicalJson.bytes(request)));

        var responseBytes = ArgumentCaptor.forClass(byte[].class);
        verify(kafka).send(eq(KafkaRpcServer.RESPONSES_TOPIC), eq("corr-1"), responseBytes.capture());
        var response = CanonicalJson.read(responseBytes.getValue());
        @SuppressWarnings("unchecked")
        var result = (Map<String, Object>) response.get("result");
        @SuppressWarnings("unchecked")
        var bindings = (List<Map<String, Object>>) result.get("pending_decisions");
        assertThat(bindings).singleElement().satisfies(binding -> assertThat(binding)
                .containsEntry("operation_id", pending.getOperationId())
                .containsEntry("pending_decision_id", pending.getId())
                .containsEntry("run_id", pending.getRunId())
                .containsEntry("business_decision", "approved")
                .containsEntry("status", "pending"));
        assertThat(((Number) bindings.getFirst().get("task_version")).longValue())
                .isEqualTo(pending.getTaskVersion());
        assertThat(response.get("signature")).isEqualTo(
                MessageCodec.signEnvelope(properties.internalHmacKey(), response));
    }
}
