package com.caijiatai.procurement.agent;

public interface AgentDispatcher {
    AgentClient.DispatchResult dispatch(AgentCommand command);

    default boolean isAsync() {
        return false;
    }
}
