package com.caijiatai.procurement.contract;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

/** 合同变更语义（H1/M4/M6 修复）：驳回恢复变更前状态；变更携带修订值并落定；重新草拟出口。 */
class ContractChangeTest {

    private static Contract executingContract() {
        var contract = Contract.derive(
                "task-1", "CT-RFQ-1", "供应商A", "物料X", new BigDecimal("7500"), 15);
        contract.applyDraft("一、合同金额为人民币 7500 元。\n二、交期 15 天。",
                List.of(Map.of("title", "金额条款"), Map.of("title", "交期条款")),
                Map.of("consistent", true), null);
        contract.submit(null);
        contract.approve("ok");
        contract.execute();
        return contract;
    }

    @Test
    void rejectFromChangeRequestRestoresPriorStatusInsteadOfDraft() {
        var contract = executingContract();
        contract.requestChange("价格调整", new BigDecimal("8000"), 20);
        assertEquals(ContractStatus.CHANGE_REQUEST.wireValue(), contract.getStatus());
        assertEquals(new BigDecimal("8000"), contract.pendingAmount());
        assertEquals(20, contract.pendingLeadDays());

        contract.reject("驳回变更");
        assertEquals(ContractStatus.EXECUTING.wireValue(), contract.getStatus());
        assertEquals(new BigDecimal("7500"), contract.getAmount()); // 原金额未被改动
    }

    @Test
    void rejectFromChangeRequestOfEffectiveContractRestoresEffective() {
        var draft = Contract.derive(
                "task-2", "CT-RFQ-2", "供应商B", "物料Y", new BigDecimal("5200"), 10);
        draft.submit(null);
        draft.approve("ok"); // EFFECTIVE（未执行）
        draft.requestChange("交期调整", new BigDecimal("5200"), 12);
        draft.reject("驳回");
        assertEquals(ContractStatus.EFFECTIVE.wireValue(), draft.getStatus());
    }

    @Test
    void rejectFromPendingApprovalStillReturnsToDraft() {
        var contract = Contract.derive(
                "task-3", "CT-RFQ-3", "供应商C", "物料Z", new BigDecimal("3000"), 7);
        contract.submit(null);
        contract.reject("退回修改");
        assertEquals(ContractStatus.DRAFT.wireValue(), contract.getStatus());
    }

    @Test
    void approveChangeAppliesPendingValuesAndMarksHistoryApplied() {
        var contract = executingContract();
        contract.requestChange("调价并延交期", new BigDecimal("8000"), 20);
        contract.applyPendingChange();
        contract.approve("同意变更");
        assertEquals(ContractStatus.EFFECTIVE.wireValue(), contract.getStatus());
        assertEquals(new BigDecimal("8000"), contract.getAmount());
        assertEquals(20, contract.getLeadDays());

        var history = contract.getChangeHistory();
        assertTrue(!history.isEmpty());
        var last = history.get(history.size() - 1);
        assertEquals(true, last.get("applied"));
        assertTrue(last.containsKey("from_status"));
        assertTrue(last.containsKey("new_amount"));
        assertTrue(last.containsKey("new_lead_days"));
        assertTrue(last.containsKey("clauses")); // 旧条款快照留痕
    }

    @Test
    void pendingValuesAreNullWhenNoChangeRequested() {
        var contract = Contract.derive(
                "task-4", "CT-RFQ-4", "供应商D", "物料W", new BigDecimal("1000"), 5);
        assertEquals(null, contract.pendingAmount());
        assertEquals(null, contract.pendingLeadDays());
    }
}
