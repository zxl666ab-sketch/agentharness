-- Recover approvals that reached a terminal Agent command state before the
-- control plane could close their pending decision and human review.
-- Keep the comparison snapshot so a buyer can inspect it and submit a new
-- formal decision; never turn a failed command into an approval automatically.
INSERT INTO procurement_audit_event (
    id, task_id, quote_id, run_id, event_type, actor, payload, created_at
)
SELECT
    REPLACE(UUID(), '-', ''),
    pending.task_id,
    pending.quote_id,
    pending.run_id,
    'procurement_decision_failed',
    'migration',
    JSON_OBJECT(
        'pending_decision_id', pending.id,
        'operation_id', pending.operation_id,
        'error', COALESCE(command.last_error, 'Agent 正式决定命令已终止')
    ),
    CURRENT_TIMESTAMP(6)
FROM pending_decision pending
JOIN agent_command command
  ON command.operation_id = pending.operation_id
 AND command.operation_type = 'approve_decision'
 AND command.status IN ('failed', 'cancelled')
LEFT JOIN procurement_decision decision
  ON decision.pending_decision_id = pending.id
WHERE pending.status IN ('pending', 'approved')
  AND decision.id IS NULL;

UPDATE review_record review
JOIN pending_decision pending
  ON pending.id = review.pending_decision_id
JOIN agent_command command
  ON command.operation_id = pending.operation_id
 AND command.operation_type = 'approve_decision'
 AND command.status IN ('failed', 'cancelled')
LEFT JOIN procurement_decision decision
  ON decision.pending_decision_id = pending.id
SET review.status = 'STALE',
    review.stale_reason = LEFT(
        COALESCE(command.last_error, 'Agent 正式决定命令已终止'),
        100
    ),
    review.updated_at = CURRENT_TIMESTAMP(6),
    review.version = review.version + 1
WHERE review.status = 'PENDING'
  AND pending.status IN ('pending', 'approved')
  AND decision.id IS NULL;

UPDATE procurement_task task
JOIN pending_decision pending
  ON pending.task_id = task.id
 AND pending.snapshot_id = task.current_snapshot_id
JOIN agent_command command
  ON command.operation_id = pending.operation_id
 AND command.operation_type = 'approve_decision'
 AND command.status IN ('failed', 'cancelled')
LEFT JOIN procurement_decision decision
  ON decision.pending_decision_id = pending.id
SET task.status = 'analyzed',
    task.updated_at = CURRENT_TIMESTAMP(6),
    task.version = task.version + 1
WHERE task.status = 'approval_pending'
  AND pending.status IN ('pending', 'approved')
  AND decision.id IS NULL;

UPDATE pending_decision pending
JOIN agent_command command
  ON command.operation_id = pending.operation_id
 AND command.operation_type = 'approve_decision'
 AND command.status IN ('failed', 'cancelled')
LEFT JOIN procurement_decision decision
  ON decision.pending_decision_id = pending.id
SET pending.status = 'stale',
    pending.updated_at = CURRENT_TIMESTAMP(6)
WHERE pending.status IN ('pending', 'approved')
  AND decision.id IS NULL;
