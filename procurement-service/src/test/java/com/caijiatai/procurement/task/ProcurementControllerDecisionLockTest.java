package com.caijiatai.procurement.task;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.caijiatai.procurement.api.ApiException;
import com.caijiatai.procurement.approval.ApprovalService;
import com.caijiatai.procurement.cache.DecisionLock;
import com.caijiatai.procurement.cache.InsightsCache;
import com.caijiatai.procurement.cache.NoopDecisionLock;
import java.util.Optional;
import org.junit.jupiter.api.Test;

/**
 * K4 锁竞争测试：并发审批请求只有一个成功（冻结设计 4.9 三层防护之分布式锁）。
 */
class ProcurementControllerDecisionLockTest {
    private final ProcurementTaskService service = mock(ProcurementTaskService.class);
    private final ApprovalService approvals = mock(ApprovalService.class);
    private final DecisionLock lock = mock(DecisionLock.class);
    private final InsightsCache cache = mock(InsightsCache.class);
    private final ProcurementController controller = new ProcurementController(service, approvals, lock, cache);

    private final ProcurementDtos.Decision body = new ProcurementDtos.Decision(
            "approved", "snap-1", "a".repeat(64), "quote-1", true, "ok");

    @Test
    void concurrentDecisionRequestsOnlyOneProceeds() {
        var first = Optional.of(DecisionLock.NOOP_HANDLE);
        when(lock.acquire("t1")).thenReturn(first, Optional.empty());
        var command = mock(com.caijiatai.procurement.agent.AgentCommand.class);
        when(command.getOperationId()).thenReturn("op-1");
        var pending = mock(com.caijiatai.procurement.approval.PendingDecision.class);
        when(pending.getRunId()).thenReturn("run-1");
        var result = new ApprovalService.RequestResult(command, pending, null);
        when(approvals.request(eq("t1"), eq(body), eq("key-1"))).thenReturn(result);

        var accepted = controller.decision("t1", body, "key-1");
        assertThat(accepted.getStatusCode().value()).isEqualTo(202);
        // 第二个请求：锁被持有 → 409 decision_lock_held，不进入审批
        assertThatThrownBy(() -> controller.decision("t1", body, "key-2"))
                .isInstanceOf(ApiException.class)
                .satisfies(error -> {
                    var api = (ApiException) error;
                    assertThat(api.code()).isEqualTo("decision_lock_held");
                    assertThat(api.status().value()).isEqualTo(409);
                });
        verify(approvals, times(1)).request(any(), any(), any());
    }

    @Test
    void lockIsReleasedInFinallyEvenWhenApprovalFails() {
        when(lock.acquire("t1")).thenAnswer(invocation -> Optional.of(DecisionLock.NOOP_HANDLE));
        when(approvals.request(eq("t1"), eq(body), any()))
                .thenThrow(new ApiException(org.springframework.http.HttpStatus.CONFLICT,
                        "stale_approval", "审批证据已失效"));

        assertThatThrownBy(() -> controller.decision("t1", body, "key-1"))
                .isInstanceOf(ApiException.class);
        // try-with-resources 保证 close 被调用
        verify(lock, times(1)).acquire("t1");
    }

    @Test
    void noopFallbackProceedsWithoutRedis() {
        var noopLock = new NoopDecisionLock();
        var noopController = new ProcurementController(service, approvals, noopLock, cache);
        var command = mock(com.caijiatai.procurement.agent.AgentCommand.class);
        when(command.getOperationId()).thenReturn("op-1");
        var pending = mock(com.caijiatai.procurement.approval.PendingDecision.class);
        when(pending.getRunId()).thenReturn("run-1");
        var result = new ApprovalService.RequestResult(command, pending, null);
        when(approvals.request(eq("t1"), eq(body), eq("key-1"))).thenReturn(result);

        var accepted = noopController.decision("t1", body, "key-1");

        assertThat(accepted.getStatusCode().value()).isEqualTo(202);
        verify(approvals, times(1)).request(eq("t1"), eq(body), eq("key-1"));
    }

    @Test
    void approvalServiceIsNeverTouchedWhenLockIsHeld() {
        when(lock.acquire("t1")).thenReturn(Optional.empty());

        assertThatThrownBy(() -> controller.decision("t1", body, "key-1"))
                .isInstanceOf(ApiException.class);
        verify(approvals, never()).request(any(), any(), any());
    }
}
