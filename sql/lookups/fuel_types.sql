-- fuel_types: 2-letter fuel code -> description. Also used by tests.sql to normalise
-- fuel values that arrive as descriptions ("Electric") instead of codes ("EL").
CREATE OR REPLACE TABLE lk_fuel_types AS
SELECT
    type_code AS fuel_type_code,
    fuel_type
FROM read_csv(
    ${input_file},
    header = true,
    delim = '|',
    quote = '',
    escape = '',
    columns = {'type_code': 'VARCHAR', 'fuel_type': 'VARCHAR'},
    store_rejects = true,
    rejects_table = 'rej_fuel_types',
    rejects_scan = 'rej_fuel_types_scan'
)
ORDER BY fuel_type_code;

COPY lk_fuel_types TO ${output_file} (FORMAT parquet, COMPRESSION zstd);

SELECT
    (SELECT count(*) FROM lk_fuel_types) AS rows_out,
    (SELECT count(DISTINCT (scan_id, file_id, line)) FROM rej_fuel_types) AS rejected_rows;
