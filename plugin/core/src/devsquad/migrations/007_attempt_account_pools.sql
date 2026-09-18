ALTER TABLE attempts ADD COLUMN account_pool_id TEXT;

CREATE INDEX attempts_active_account_pool
ON attempts(account_pool_id, status)
WHERE account_pool_id IS NOT NULL;
