package com.caijiatai.procurement.contract;

import com.caijiatai.procurement.platform.statemachine.StateMachine;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * 合同状态机定义（P3-2）：DRAFT → PENDING_APPROVAL → EFFECTIVE → EXECUTING → CLOSED
 * + CHANGE_REQUEST（变更重新审批）；在 OrderStateMachineConfig 的平台注册表中注册。
 */
@Configuration
public class ContractStateMachineConfig {
    public static final String CONTRACT_MACHINE = "contract";

    @Bean
    public StateMachine<ContractStatus, ContractEvent> contractStateMachine() {
        return StateMachine.define(ContractStatus.class, ContractEvent.class)
                .permit(ContractStatus.DRAFT, ContractEvent.SUBMIT, ContractStatus.PENDING_APPROVAL)
                .permit(ContractStatus.PENDING_APPROVAL, ContractEvent.APPROVE, ContractStatus.EFFECTIVE)
                .permit(ContractStatus.PENDING_APPROVAL, ContractEvent.REJECT, ContractStatus.DRAFT)
                .permit(ContractStatus.EFFECTIVE, ContractEvent.EXECUTE, ContractStatus.EXECUTING)
                .permit(ContractStatus.EXECUTING, ContractEvent.CLOSE, ContractStatus.CLOSED)
                .permit(ContractStatus.EFFECTIVE, ContractEvent.REQUEST_CHANGE, ContractStatus.CHANGE_REQUEST)
                .permit(ContractStatus.EXECUTING, ContractEvent.REQUEST_CHANGE, ContractStatus.CHANGE_REQUEST)
                .permit(ContractStatus.CHANGE_REQUEST, ContractEvent.APPROVE, ContractStatus.EFFECTIVE)
                // 变更驳回的注册目标仅是 can()/transition() 门禁的名义值：Contract.reject()
                // 按 change_history 快照恢复变更前状态（快照缺失兜底 EFFECTIVE）。旧实现把
                // (CHANGE_REQUEST, REJECT)→EXECUTING 再注册一次，靠 map 覆盖顺序"生效"，
                // 现引擎禁止重复注册（审查报告 2026-08-28 S2），此处只保留一条。
                .permit(ContractStatus.CHANGE_REQUEST, ContractEvent.REJECT, ContractStatus.EFFECTIVE)
                .build();
    }
}
