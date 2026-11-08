-- Validation metrics over the written parquet (not the staging tables), so validation checks
-- exactly what downstream consumers will read. validate.py applies thresholds to this one row.
--
-- Placeholders (filled by validate.py): tests_glob, items_glob, item_detail_file = parquet
-- paths, year = the year being validated.

CREATE OR REPLACE VIEW v_tests AS
SELECT * FROM read_parquet(${tests_glob}, hive_partitioning = false);

CREATE OR REPLACE VIEW v_items AS
SELECT * FROM read_parquet(${items_glob}, hive_partitioning = false);

CREATE OR REPLACE VIEW v_item_detail AS
SELECT * FROM read_parquet(${item_detail_file});

SELECT
    -- tests
    (SELECT count(*) FROM v_tests) AS tests_rows,
    (SELECT count(*) - count(DISTINCT test_id) FROM v_tests) AS tests_duplicate_ids,
    (SELECT count(*) FILTER (WHERE test_id IS NULL) FROM v_tests) AS tests_null_test_id,
    (SELECT count(*) FILTER (WHERE test_date IS NULL) FROM v_tests) AS tests_null_test_date,
    (SELECT count(*) FILTER (WHERE test_result IS NULL) FROM v_tests) AS tests_null_test_result,
    (SELECT count(*) FILTER (WHERE test_class_id IS NULL) FROM v_tests) AS tests_null_test_class_id,
    (SELECT count(*) FILTER (WHERE year(test_date) <> ${year} OR test_year <> ${year})
        FROM v_tests) AS tests_outside_year,
    (SELECT min(test_date) FROM v_tests) AS tests_min_date,
    (SELECT max(test_date) FROM v_tests) AS tests_max_date,

    -- failure items
    (SELECT count(*) FROM v_items) AS items_rows,
    (SELECT count(*) FILTER (WHERE test_id IS NULL) FROM v_items) AS items_null_test_id,
    (SELECT count(*) FILTER (WHERE rfr_id IS NULL) FROM v_items) AS items_null_rfr_id,
    (SELECT count(*) FILTER (WHERE rfr_type_code IS NULL) FROM v_items) AS items_null_rfr_type_code,
    (SELECT count(*) FILTER (WHERE year(completed_at) <> ${year} OR test_year <> ${year})
        FROM v_items) AS items_outside_year,

    -- relationships
    (SELECT count(*) FROM v_items AS i SEMI JOIN v_tests AS t USING (test_id))
        AS items_joined_to_tests,
    (SELECT count(*) FROM v_tests WHERE test_result = 'F') AS failed_tests,
    (SELECT count(*) FROM v_tests AS t
        WHERE t.test_result = 'F'
          AND EXISTS (
              SELECT 1 FROM v_items AS i WHERE i.test_id = t.test_id AND i.rfr_type_code = 'F'
          )) AS failed_tests_with_fail_item,
    (SELECT count(*)
        FROM v_items AS i
        JOIN v_tests AS t USING (test_id)
        SEMI JOIN v_item_detail AS d
            ON d.rfr_id = i.rfr_id AND d.test_class_id = t.test_class_id
    ) AS items_joined_to_item_detail;
