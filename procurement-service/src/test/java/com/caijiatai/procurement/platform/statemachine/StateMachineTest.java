package com.caijiatai.procurement.platform.statemachine;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

class StateMachineTest {
    private enum State { A, B, C }
    private enum Event { TO_B, TO_C, BAD }

    @Test
    void permitsRegisteredTransitionsAndReturnsTarget() {
        var machine = StateMachine.define(State.class, Event.class)
                .permit(State.A, Event.TO_B, State.B)
                .permit(State.B, Event.TO_C, State.C)
                .build();

        assertThat(machine.can(State.A, Event.TO_B)).isTrue();
        assertThat(machine.transition("biz-1", State.A, Event.TO_B, Map.of())).isEqualTo(State.B);
        assertThat(machine.transition("biz-1", State.B, Event.TO_C, Map.of())).isEqualTo(State.C);
    }

    @Test
    void rejectsIllegalTransitionsWithIllegalStateTransition() {
        var machine = StateMachine.define(State.class, Event.class)
                .permit(State.A, Event.TO_B, State.B)
                .build();

        assertThat(machine.can(State.C, Event.TO_B)).isFalse();
        assertThatThrownBy(() -> machine.transition("biz-1", State.C, Event.TO_B, Map.of()))
                .isInstanceOf(IllegalStateTransition.class)
                .satisfies(error -> {
                    var illegal = (IllegalStateTransition) error;
                    assertThat(illegal.businessId()).isEqualTo("biz-1");
                    assertThat(illegal.from()).isEqualTo(State.C);
                    assertThat(illegal.event()).isEqualTo(Event.TO_B);
                });
        // 已注册状态但未注册的事件同样拒绝
        assertThatThrownBy(() -> machine.transition("biz-1", State.A, Event.BAD, Map.of()))
                .isInstanceOf(IllegalStateTransition.class);
    }

    @Test
    void runsActionHooksWithBusinessContext() {
        var calls = new ArrayList<String>();
        var machine = StateMachine.define(State.class, Event.class)
                .permit(State.A, Event.TO_B, State.B, (businessId, from, event, args) ->
                        calls.add(businessId + ":" + from + ":" + event + ":" + args.get("note")))
                .build();

        machine.transition("biz-9", State.A, Event.TO_B, Map.of("note", "hello"));

        assertThat(calls).containsExactly("biz-9:A:TO_B:hello");
    }

    @Test
    void registryHoldsNamedMachinesForPlatformExtension() {
        var registry = new StateMachineRegistry();
        var machine = StateMachine.define(State.class, Event.class)
                .permit(State.A, Event.TO_B, State.B)
                .build();
        registry.register("demo", machine);

        assertThat(registry.find("demo")).contains(machine);
        assertThat(registry.find("missing")).isEmpty();
        assertThat(registry.describe()).containsKeys("demo");
    }

    @Test
    void orderLikeMachineSupportsBothCloseSemantics() {
        // 冻结设计 4.3：PENDING_SHIPMENT close=取消；RECEIVED close=完成
        var machine = StateMachine.define(State.class, Event.class)
                .permit(State.A, Event.TO_B, State.B)
                .permit(State.A, Event.TO_C, State.C)
                .permit(State.B, Event.TO_C, State.C)
                .build();
        assertThat(machine.transition("order-1", State.A, Event.TO_C, Map.of())).isEqualTo(State.C);
        assertThat(machine.transition("order-1", State.A, Event.TO_B, Map.of())).isEqualTo(State.B);
        assertThat(machine.transition("order-1", State.B, Event.TO_C, Map.of())).isEqualTo(State.C);
        // SHIPPED 不允许直接关闭（未注册）
        assertThat(machine.can(State.B, Event.TO_C)).isTrue();
    }

    @Test
    void nullArgsAreTolerated() {
        var machine = StateMachine.define(State.class, Event.class)
                .permit(State.A, Event.TO_B, State.B)
                .build();
        assertThat(machine.transition("biz-1", State.A, Event.TO_B, null)).isEqualTo(State.B);
    }

    @Test
    void builtMachineIsImmutable() {
        var machine = StateMachine.define(State.class, Event.class)
                .permit(State.A, Event.TO_B, State.B)
                .build();
        assertThat(machine.can(State.B, Event.TO_C)).isFalse();
        assertThat(List.of()).isEmpty();
    }

    @Test
    void duplicateSourceEventRegistrationFailsFast() {
        // 旧报告 S2：map.put 静默覆盖 → 注册顺序决定语义。现在必须在定义期抛错。
        assertThatThrownBy(() -> StateMachine.define(State.class, Event.class)
                .permit(State.A, Event.TO_B, State.B)
                .permit(State.A, Event.TO_B, State.C))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("重复注册流转");
    }

    @Test
    void sameEventFromDifferentSourcesIsAllowed() {
        var machine = StateMachine.define(State.class, Event.class)
                .permit(State.A, Event.TO_C, State.C)
                .permit(State.B, Event.TO_C, State.C)
                .build();
        assertThat(machine.transition("o1", State.A, Event.TO_C, Map.of())).isEqualTo(State.C);
        assertThat(machine.transition("o2", State.B, Event.TO_C, Map.of())).isEqualTo(State.C);
    }
}
