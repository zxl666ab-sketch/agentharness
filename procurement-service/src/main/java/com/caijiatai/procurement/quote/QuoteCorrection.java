package com.caijiatai.procurement.quote;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.Map;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

@Entity
@Table(name = "quote_correction")
public class QuoteCorrection {
    @Id
    @Column(length = 32)
    private String id;
    @Column(name = "task_id", nullable = false, length = 32)
    private String taskId;
    @Column(name = "quote_id", nullable = false, length = 32)
    private String quoteId;
    @Column(name = "field_name", nullable = false, length = 100)
    private String fieldName;
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "old_value", columnDefinition = "json")
    private Object oldValue;
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "new_value", columnDefinition = "json")
    private Object newValue;
    @Column(name = "chosen_from_conflicts", nullable = false)
    private boolean chosenFromConflicts;
    @Column(nullable = false, length = 100)
    private String actor;
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    protected QuoteCorrection() {}

    public static QuoteCorrection create(
            String taskId, String quoteId, String field, Object oldValue, Object newValue,
            boolean chosenFromConflicts, String actor) {
        var correction = new QuoteCorrection();
        correction.id = java.util.UUID.randomUUID().toString().replace("-", "");
        correction.taskId = taskId;
        correction.quoteId = quoteId;
        correction.fieldName = field;
        correction.oldValue = oldValue;
        correction.newValue = newValue;
        correction.chosenFromConflicts = chosenFromConflicts;
        correction.actor = actor;
        correction.createdAt = Instant.now();
        return correction;
    }

    public boolean isChosenFromConflicts() {
        return chosenFromConflicts;
    }

    public String getId() { return id; }
    public String getTaskId() { return taskId; }
    public String getQuoteId() { return quoteId; }
    public String getFieldName() { return fieldName; }
    public Object getOldValue() { return oldValue; }
    public Object getNewValue() { return newValue; }
    public String getActor() { return actor; }
    public Instant getCreatedAt() { return createdAt; }
}
