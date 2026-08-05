package com.caijiatai.procurement.task;

public enum TaskStatus {
    DRAFT("draft"),
    COLLECTING("collecting"),
    REVIEW("review"),
    READY("ready"),
    ANALYZING("analyzing"),
    ANALYZED("analyzed"),
    APPROVAL_PENDING("approval_pending"),
    APPROVED("approved"),
    NO_AWARD("no_award"),
    CANCELLED("cancelled");

    private final String wireValue;

    TaskStatus(String wireValue) {
        this.wireValue = wireValue;
    }

    public String wireValue() {
        return wireValue;
    }
}
