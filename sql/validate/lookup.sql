-- Row count, null keys and duplicate keys for one lookup table.
-- Placeholders (filled by validate.py): file = parquet path, key = SQL key expression,
-- e.g. "rfr_id, test_class_id"; key_is_null = e.g. "rfr_id IS NULL OR test_class_id IS NULL".
SELECT
    count(*) AS rows,
    count(*) FILTER (WHERE ${key_is_null}) AS null_keys,
    count(*) - count(DISTINCT (${key})) AS duplicate_keys
FROM read_parquet(${file});
