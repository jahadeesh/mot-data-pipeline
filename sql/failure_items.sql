-- failure_items: defects (RfRs) recorded against a test, partitioned by test_year.
-- Foreign key: test_id -> tests.test_id. Run after tests.sql (sample mode filters on tests_final).
--
-- Placeholders (filled by transform.py): input_files = list of CSV paths, output_dir = table
-- directory, year = release year, items_filter = empty, or a WHERE clause in sample mode.

-- 1. Stage. Column names differ from the user guide (rfr_type_code,
--    mot_test_rfr_location_type_id, dangerous_mark) and there is an extra completed_date.
--    Same CSV dialect as the results files: backslash escapes, repeated header lines.
CREATE OR REPLACE TABLE raw_items AS
SELECT *
FROM read_csv(
    ${input_files},
    header = true,
    delim = ',',
    quote = '"',
    escape = '\',
    columns = {
        'test_id': 'BIGINT',
        'rfr_id': 'INTEGER',
        'rfr_type_code': 'VARCHAR',
        'mot_test_rfr_location_type_id': 'INTEGER',
        'dangerous_mark': 'VARCHAR',
        'completed_date': 'TIMESTAMP'
    },
    store_rejects = true,
    rejects_table = 'rej_items',
    rejects_scan = 'rej_items_scan'
);

-- 2. Drop exact whole-row duplicates only. The same RfR at a *different* location is a
--    genuine second defect and is kept. (dangerous_mark is only ever 'D' or empty, so the
--    boolean mapping does not merge distinct source rows.)
CREATE OR REPLACE TABLE items_final AS
SELECT DISTINCT
    test_id,
    rfr_id,
    rfr_type_code,
    mot_test_rfr_location_type_id AS location_id,
    coalesce(dangerous_mark = 'D', false) AS is_dangerous,
    completed_date AS completed_at,
    CAST(${year} AS SMALLINT) AS test_year
FROM raw_items
${items_filter};

-- 3. Write partitioned parquet.
COPY items_final TO ${output_dir} (
    FORMAT parquet,
    COMPRESSION zstd,
    PARTITION_BY (test_year),
    WRITE_PARTITION_COLUMNS true,
    OVERWRITE_OR_IGNORE true,
    FILENAME_PATTERN 'part_{i}'
);

-- 4. Stats for the validation report.
SELECT
    (SELECT count(*) FROM raw_items) AS raw_rows,
    (SELECT count(*) FROM raw_items ${items_filter}) AS rows_in_scope,
    (SELECT count(*) FROM items_final) AS rows_out,
    (SELECT count(DISTINCT (scan_id, file_id, line)) FROM rej_items
        WHERE csv_line NOT LIKE 'test_id,%') AS rejected_rows,
    (SELECT count(DISTINCT (scan_id, file_id, line)) FROM rej_items
        WHERE csv_line LIKE 'test_id,%') AS repeated_header_rows,
    (SELECT count(*) FROM raw_items WHERE dangerous_mark IS NOT NULL AND dangerous_mark <> 'D')
        AS unexpected_dangerous_marks;
