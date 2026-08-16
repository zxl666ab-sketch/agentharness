-- P2-2: 冲突裁决流程化 —— 人工修正若来自冲突候选值单选，则落库标记
ALTER TABLE quote_correction
    ADD COLUMN chosen_from_conflicts boolean NOT NULL DEFAULT FALSE;
