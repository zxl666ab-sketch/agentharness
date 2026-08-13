package com.caijiatai.procurement.platform.statemachine;

import java.util.HashMap;
import java.util.Map;

/**
 * 注册式通用状态机引擎（冻结设计 docs/platform-upgrade-design.md 4.2）。
 *
 * <p>平台叙事：新增业务只需定义自己的状态/事件枚举并注册流转表，引擎统一校验与动作钩子。
 * 非法流转抛 {@link IllegalStateTransition}（409）；并发由调用方乐观锁兜底。
 * 现有 TaskStatus 状态机为历史实现，不迁移。
 *
 * @param <S> 状态枚举
 * @param <E> 事件枚举
 */
public final class StateMachine<S extends Enum<S>, E extends Enum<E>> {
    private final Map<S, Map<E, Transition<S, E>>> table;

    private StateMachine(Map<S, Map<E, Transition<S, E>>> table) {
        this.table = table;
    }

    public static <S extends Enum<S>, E extends Enum<E>> Builder<S, E> define(
            Class<S> states, Class<E> events) {
        return new Builder<>(states, events);
    }

    /** 是否允许 from --event--> 流转。 */
    public boolean can(S from, E event) {
        var byEvent = table.get(from);
        return byEvent != null && byEvent.containsKey(event);
    }

    /**
     * 校验并执行流转，返回目标状态。
     *
     * @throws IllegalStateTransition 非法流转
     */
    public S transition(String businessId, S from, E event, Map<String, Object> args) {
        var byEvent = table.get(from);
        if (byEvent == null || !byEvent.containsKey(event)) {
            throw new IllegalStateTransition(businessId, from, event);
        }
        var transition = byEvent.get(event);
        if (transition.action() != null) {
            transition.action().apply(businessId, from, event, args == null ? Map.of() : args);
        }
        return transition.target();
    }

    /** 流转表项：目标状态 + 可选动作钩子。 */
    public record Transition<S extends Enum<S>, E extends Enum<E>>(
            S target, StateTransitionHook<S, E> action) {}

    public interface StateTransitionHook<S extends Enum<S>, E extends Enum<E>> {
        void apply(String businessId, S from, E event, Map<String, Object> args);
    }

    public static final class Builder<S extends Enum<S>, E extends Enum<E>> {
        private final Map<S, Map<E, Transition<S, E>>> table = new HashMap<>();

        private Builder(Class<S> states, Class<E> events) {
            for (var state : states.getEnumConstants()) {
                table.put(state, new HashMap<>());
            }
        }

        public Builder<S, E> permit(S from, E event, S to) {
            return permit(from, event, to, null);
        }

        public Builder<S, E> permit(S from, E event, S to, StateTransitionHook<S, E> action) {
            table.get(from).put(event, new Transition<>(to, action));
            return this;
        }

        public StateMachine<S, E> build() {
            var frozen = new HashMap<S, Map<E, Transition<S, E>>>();
            table.forEach((state, byEvent) -> frozen.put(state, Map.copyOf(byEvent)));
            return new StateMachine<>(Map.copyOf(frozen));
        }
    }
}
