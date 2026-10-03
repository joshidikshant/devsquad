-- Contract epoch only: Council's read/claim/decision authority is not safe for
-- schema-16 packages sharing this ledger. Existing exclusive migration,
-- active/recoverable deferral and per-connection write triggers fence them.
-- No table, scheduling service or historical outcome representation changes.
SELECT 1;
