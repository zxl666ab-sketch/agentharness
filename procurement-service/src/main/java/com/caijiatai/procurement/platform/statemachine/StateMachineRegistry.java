package com.caijiatai.procurement.platform.statemachine;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Optional;
import org.springframework.stereotype.Component;

/**
 * 状态机注册表（冻结设计 4.2）：订单/对账状态机在此注册，业务代码通过名称取用。
 * 平台扩展叙事：新业务注册自己的状态机即可复用引擎与审计钩子。
 */
@Component
public final class StateMachineRegistry {
    private final Map<String, StateMachine<?, ?>> machines = new LinkedHashMap<>();

    public void register(String name, StateMachine<?, ?> machine) {
        machines.put(name, machine);
    }

    public Optional<StateMachine<?, ?>> find(String name) {
        return Optional.ofNullable(machines.get(name));
    }

    public Map<String, String> describe() {
        var value = new LinkedHashMap<String, String>();
        machines.forEach((name, machine) -> value.put(name, machine.toString()));
        return value;
    }
}
