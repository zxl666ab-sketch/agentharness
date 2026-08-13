package com.caijiatai.procurement.agent;

import java.util.Map;

public interface AgentDispatcher {
    DispatchResult dispatch(AgentCommand command);

    default boolean isAsync() {
        return false;
    }

    record DispatchResult(int status, Map<String, Object> body) {}

    final class AgentUnavailableException extends RuntimeException {
        public AgentUnavailableException(String message, Throwable cause) {
            super(message, cause);
        }
    }
}
