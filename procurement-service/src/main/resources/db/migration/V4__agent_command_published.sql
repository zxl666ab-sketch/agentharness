ALTER TABLE agent_command
    ADD COLUMN published_at datetime(6) NULL AFTER completed_at;
