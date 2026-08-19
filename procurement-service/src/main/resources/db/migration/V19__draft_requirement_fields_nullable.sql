-- Draft and WAITING_HUMAN tasks must preserve missing requirements as unknown facts.
-- Confirmed requirements remain guarded by Java's RequirementValidator.
ALTER TABLE procurement_task
    MODIFY COLUMN quantity decimal(60, 18) NULL,
    MODIFY COLUMN unit varchar(50) NULL;
