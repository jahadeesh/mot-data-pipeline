-- test_types: tests.test_type code -> description (NT, RT, PL, PV, ES, EI).
CREATE OR REPLACE TABLE lk_test_types AS
SELECT
    type_code AS test_type_code,
    test_type
FROM read_csv(
    ${input_file},
    header = true,
    delim = '|',
    quote = '',
    escape = '',
    columns = {'type_code': 'VARCHAR', 'test_type': 'VARCHAR'},
    store_rejects = true,
    rejects_table = 'rej_test_types',
    rejects_scan = 'rej_test_types_scan'
)
ORDER BY test_type_code;

COPY lk_test_types TO ${output_file} (FORMAT parquet, COMPRESSION zstd);

SELECT
    (SELECT count(*) FROM lk_test_types) AS rows_out,
    (SELECT count(DISTINCT (scan_id, file_id, line)) FROM rej_test_types) AS rejected_rows;
