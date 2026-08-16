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
                .permit(ContractStatus.CHANGE_REQUEST, ContractEvent.REJECT, ContractStatus.EFFECTIVE)
                .build();
    }
}
