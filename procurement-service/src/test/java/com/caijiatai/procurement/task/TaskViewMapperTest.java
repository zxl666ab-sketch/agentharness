package com.caijiatai.procurement.task;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

class TaskViewMapperTest {
    @Test
    void draftViewKeepsUnknownRequirementFieldsNullUntilTheUserAnswers() {
        var view = new TaskViewMapper().summary(
                ProcurementTask.draft("帮我采购一批白色快递袋"), 0, 0, null);

        assertThat(view)
                .containsEntry("item_name", "待识别")
                .containsEntry("quantity", null)
                .containsEntry("unit", null);
    }
}
