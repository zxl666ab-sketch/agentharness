-- Data repair: an AI step record must never stay open after its task reached a terminal state.
--
-- Java consumed `ai_task.step` events on caijiatai.events, but the Python Agent never
-- produced them, so the only record each task ever had was the creation-time placeholder
-- ("等待 Agent 接收任务", PENDING, attempt N / sequence 0). The workbench renders every
-- non-terminal step with a spinner, so succeeded tasks kept spinning in 执行步骤 forever.
-- AiTaskService now reconciles the timeline on every terminal transition; this migration
-- applies the same rule to the rows that were already stuck.
UPDATE ai_task_record r
JOIN ai_task t ON t.id = r.ai_task_id
SET r.status = CASE
        WHEN t.status = 'SUCCEEDED' THEN 'SUCCEEDED'
        WHEN t.status = 'CANCELLED' THEN 'SKIPPED'
        ELSE 'FAILED'
    END,
    r.summary = CASE
        WHEN t.status = 'SUCCEEDED' THEN 'Agent 已完成该步骤'
        WHEN t.status = 'CANCELLED' THEN '任务已取消，未完成的步骤不再推进'
        ELSE 'AI 任务失败'
    END,
    r.error_category = CASE
        WHEN t.status = 'FAILED' THEN t.error_category ELSE NULL
    END,
    r.error_code = CASE
        WHEN t.status = 'FAILED' THEN t.error_code ELSE NULL
    END,
    r.error_message = CASE
        WHEN t.status = 'FAILED' THEN t.error_message ELSE NULL
    END,
    r.started_at = COALESCE(r.started_at, r.created_at),
    r.finished_at = COALESCE(r.finished_at, t.finished_at, t.updated_at, r.created_at),
    r.duration_ms = GREATEST(0, TIMESTAMPDIFF(
        MICROSECOND,
        COALESCE(r.started_at, r.created_at),
        COALESCE(r.finished_at, t.finished_at, t.updated_at, r.created_at)
    ) DIV 1000)
WHERE r.status IN ('PENDING', 'RUNNING')
  AND t.status IN ('SUCCEEDED', 'FAILED', 'CANCELLED');
