-- rfr_locations: decodes failure_items.location_id into lateral / longitudinal / vertical text.
-- Source: mdr_rfr_location.csv. Columns are renamed: id -> location_id.
CREATE OR REPLACE TABLE lk_rfr_locations AS
SELECT
    id AS location_id,
    "lateral",  -- LATERAL is a reserved word in DuckDB, hence the quotes
    longitudinal,
    vertical
FROM read_csv(
    ${input_file},
    header = true,
    delim = '|',
    quote = '',
    escape = '',
    columns = {
        'id': 'INTEGER',
        'lateral': 'VARCHAR',
        'longitudinal': 'VARCHAR',
        'vertical': 'VARCHAR'
    },
    store_rejects = true,
    rejects_table = 'rej_rfr_locations',
    rejects_scan = 'rej_rfr_locations_scan'
)
ORDER BY location_id;

COPY lk_rfr_locations TO ${output_file} (FORMAT parquet, COMPRESSION zstd);

SELECT
    (SELECT count(*) FROM lk_rfr_locations) AS rows_out,
    (SELECT count(DISTINCT (scan_id, file_id, line)) FROM rej_rfr_locations) AS rejected_rows;
