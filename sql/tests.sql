-- tests: one row per MOT test (test_id is unique), partitioned by test_year.
-- Requires lk_fuel_types (sql/lookups/fuel_types.sql) to exist in the same database.
--
-- Placeholders (filled by transform.py): input_files = list of CSV paths, output_dir = table
-- directory, year = release year, limit_clause = empty, or "ORDER BY ... LIMIT n" in sample mode.

-- 1. Stage the raw CSVs. The 2024 files are comma-delimited with a header and ISO dates
--    (the user guide's pipe/DD-MM-YYYY description is out of date). Quotes inside values are
--    backslash-escaped ("STREETZONE 50 2T 12\""), not doubled. Unparseable rows go to
--    rej_tests instead of failing the run; validation fails if there are too many.
--    The header line is also repeated mid-file (concatenated exports); those land in
--    rej_tests too and are counted separately below.
CREATE OR REPLACE TABLE raw_tests AS
SELECT *
FROM read_csv(
    ${input_files},
    header = true,
    delim = ',',
    quote = '"',
    escape = '\',
    columns = {
        'test_id': 'BIGINT',
        'vehicle_id': 'BIGINT',
        'test_date': 'DATE',
        'test_class_id': 'VARCHAR',
        'test_type': 'VARCHAR',
        'test_result': 'VARCHAR',
        'test_mileage': 'DOUBLE',          -- arrives as "65494.0"
        'postcode_area': 'VARCHAR',
        'make': 'VARCHAR',
        'model': 'VARCHAR',
        'colour': 'VARCHAR',
        'fuel_type': 'VARCHAR',
        'cylinder_capacity': 'BIGINT',
        'first_use_date': 'DATE',
        'completed_date': 'TIMESTAMP'      -- ISO-8601 with Z: stored as naive UTC
    },
    store_rejects = true,
    rejects_table = 'rej_tests',
    rejects_scan = 'rej_tests_scan'
);

-- 2. Deduplicate and clean.
--    The source repeats rows (exact duplicates) and occasionally has several *different* rows
--    for one test_id (e.g. colour or first_use_date differ). Keep exactly one row per test_id,
--    chosen deterministically: latest completion, then latest first_use_date, then colour A-Z.
CREATE OR REPLACE TABLE tests_final AS
WITH ranked AS (
    SELECT *
    FROM raw_tests
    QUALIFY row_number() OVER (
        PARTITION BY test_id
        ORDER BY completed_date DESC NULLS LAST, first_use_date DESC NULLS LAST, colour NULLS LAST
    ) = 1
),
fuel_names AS (
    SELECT lower(fuel_type) AS fuel_name, fuel_type_code FROM lk_fuel_types
)
SELECT
    r.test_id,
    r.vehicle_id,
    r.test_date,
    r.test_class_id,
    r.test_type,
    r.test_result,
    -- The guide says 0 or blank means "no reading taken", so both become NULL.
    CASE WHEN r.test_mileage > 0 THEN TRY_CAST(round(r.test_mileage) AS INTEGER) END
        AS test_mileage,
    r.postcode_area,
    r.make,
    r.model,
    r.colour,
    -- Some rows carry the description ("Electric") instead of the code ("EL").
    -- Unknown values are kept as-is and reported by the stats query below.
    COALESCE(by_code.fuel_type_code, by_name.fuel_type_code, r.fuel_type) AS fuel_type,
    TRY_CAST(r.cylinder_capacity AS INTEGER) AS cylinder_capacity,
    r.first_use_date,
    r.completed_date AS completed_at,
    CAST(${year} AS SMALLINT) AS test_year
FROM ranked AS r
LEFT JOIN lk_fuel_types AS by_code ON r.fuel_type = by_code.fuel_type_code
LEFT JOIN fuel_names AS by_name ON lower(r.fuel_type) = by_name.fuel_name
${limit_clause};

-- 3. Write partitioned parquet. test_year is also kept inside the files so each file is
--    self-describing (useful when files are copied or published individually).
COPY tests_final TO ${output_dir} (
    FORMAT parquet,
    COMPRESSION zstd,
    PARTITION_BY (test_year),
    WRITE_PARTITION_COLUMNS true,
    OVERWRITE_OR_IGNORE true,
    FILENAME_PATTERN 'part_{i}'
);

-- 4. Stats for the validation report.
WITH distinct_rows AS (SELECT DISTINCT * FROM raw_tests),
unknown_fuel AS (
    SELECT fuel_type AS value, count(*) AS rows
    FROM tests_final
    WHERE fuel_type IS NOT NULL
      AND fuel_type NOT IN (SELECT fuel_type_code FROM lk_fuel_types)
    GROUP BY fuel_type
)
SELECT
    (SELECT count(*) FROM raw_tests) AS raw_rows,
    (SELECT count(*) FROM distinct_rows) AS distinct_rows,
    (SELECT count(DISTINCT test_id) FROM raw_tests) AS distinct_test_ids,
    (SELECT count(*) FROM (
        SELECT test_id FROM distinct_rows GROUP BY test_id HAVING count(*) > 1
    )) AS conflicting_test_ids,
    (SELECT count(*) FROM tests_final) AS rows_out,
    (SELECT count(DISTINCT (scan_id, file_id, line)) FROM rej_tests
        WHERE csv_line NOT LIKE 'test_id,%') AS rejected_rows,
    (SELECT count(DISTINCT (scan_id, file_id, line)) FROM rej_tests
        WHERE csv_line LIKE 'test_id,%') AS repeated_header_rows,
    (SELECT coalesce(list({'value': value, 'rows': rows} ORDER BY rows DESC), [])
        FROM unknown_fuel) AS unknown_fuel_values;
