package com.caijiatai.procurement.ai;

import java.util.Set;

public enum AiTaskStatus {
    PENDING,
    DISPATCHING,
    RUNNING,
    SUCCEEDED,
    FAILED,
    RETRYING,
    CANCELLED;

    public Set<AiTaskStatus> allowedTargets() {
        return switch (this) {
            case PENDING -> Set.of(DISPATCHING, CANCELLED);
            case DISPATCHING -> Set.of(RUNNING, FAILED, CANCELLED);
            case RUNNING -> Set.of(SUCCEEDED, FAILED, CANCELLED);
            case SUCCEEDED, CANCELLED -> Set.of();
            case FAILED -> Set.of(RETRYING, CANCELLED);
            case RETRYING -> Set.of(RUNNING, FAILED, CANCELLED);
        };
    }

    public boolean canTransitionTo(AiTaskStatus target) {
        return allowedTargets().contains(target);
    }
}
