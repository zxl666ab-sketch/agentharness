-- Decisions submitted from the task detail used to bypass ReviewRecord.submit,
-- leaving completed work in the PENDING queue. Reconcile the newest review for
-- each decided task/snapshot; future decisions are handled in ReviewService.
UPDATE review_record review
JOIN procurement_decision decision
  ON decision.task_id = review.business_id
 AND decision.snapshot_id = review.snapshot_id
JOIN (
    SELECT business_id, snapshot_id, MAX(created_at) AS created_at
    FROM review_record
    WHERE status = 'PENDING'
    GROUP BY business_id, snapshot_id
) latest
  ON latest.business_id = review.business_id
 AND latest.snapshot_id = review.snapshot_id
 AND latest.created_at = review.created_at
SET review.status = CASE
        WHEN decision.decision = 'no_award' THEN 'NO_AWARD'
        ELSE 'APPROVED'
    END,
    review.action = CASE
        WHEN decision.decision = 'no_award' THEN 'NO_AWARD'
        WHEN review.suggested_quote_id <=> decision.quote_id THEN 'APPROVE_SUGGESTION'
        ELSE 'REVISE_AND_APPROVE'
    END,
    review.final_quote_id = decision.quote_id,
    review.reason = COALESCE(review.reason, decision.note),
    review.actor = COALESCE(review.actor, decision.actor),
    review.pending_decision_id = decision.pending_decision_id,
    review.decision_id = decision.id,
    review.acted_at = COALESCE(review.acted_at, decision.created_at),
    review.updated_at = GREATEST(review.updated_at, decision.created_at)
WHERE review.status = 'PENDING';
