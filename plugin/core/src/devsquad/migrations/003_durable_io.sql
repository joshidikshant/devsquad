ALTER TABLE attempts ADD COLUMN stdout_spool TEXT;
ALTER TABLE attempts ADD COLUMN stderr_spool TEXT;
ALTER TABLE attempts ADD COLUMN stdout_meta TEXT;
ALTER TABLE attempts ADD COLUMN stderr_meta TEXT;
ALTER TABLE attempts ADD COLUMN exit_record TEXT;
ALTER TABLE attempts ADD COLUMN child_record TEXT;
