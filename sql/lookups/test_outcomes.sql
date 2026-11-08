-- test_outcomes: tests.test_result code -> description (P, F, PRS, ABA, ABR, ...).
CREATE OR REPLACE TABLE lk_test_outcomes AS
SELECT
    result_code AS test_result_code,
    result AS test_result
FROM read_csv(
    ${input_file},
    header = true,
    delim = '|',
    quote = '',
    escape = '',
    columns = {'result_code': 'VARCHAR', 'result': 'VARCHAR'},
    store_rejects = true,
    rejects_table = 'rej_test_outcomes',
    rejects_scan = 'rej_test_outcomes_scan'
)
ORDER BY test_result_code;

COPY lk_test_outcomes TO ${output_file} (FORMAT parquet, COMPRESSION zstd);

SELECT
    (SELECT count(*) FROM lk_test_outcomes) AS rows_out,
    (SELECT count(DISTINCT (scan_id, file_id, line)) FROM rej_test_outcomes) AS rejected_rows;
