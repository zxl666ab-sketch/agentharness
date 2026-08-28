package com.caijiatai.procurement.contract;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

/** 旧报告 S2：重复 (CHANGE_REQUEST, REJECT) 注册在 fail-fast 引擎下会在定义期抛错。 */
class ContractStateMachineConfigTest {
    @Test
    void registersEveryTransitionExactlyOnceAndKeepsChangeReject() {
        // If a (source,event) pair were registered twice, this bean factory would throw
        // IllegalStateException now that StateMachine.Builder forbids silent overwrites.
        var machine = new ContractStateMachineConfig().contractStateMachine();

        assertThat(machine.can(ContractStatus.CHANGE_REQUEST, ContractEvent.REJECT)).isTrue();
        assertThat(machine.can(ContractStatus.CHANGE_REQUEST, ContractEvent.APPROVE)).isTrue();
        assertThat(machine.can(ContractStatus.PENDING_APPROVAL, ContractEvent.REJECT)).isTrue();
        // CLOSED 是终态：不允许再驳回
        assertThat(machine.can(ContractStatus.CLOSED, ContractEvent.REJECT)).isFalse();
    }
}
