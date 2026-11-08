-- item_detail: one row per (rfr_id, test_class_id), the "leaf" of the test item hierarchy.
-- Source: item_detail.csv in lookup.zip (pipe-delimited, with header).
-- Free-text fields contain literal double quotes, so quoting is disabled (quote = '').
CREATE OR REPLACE TABLE lk_item_detail AS
SELECT
    rfr_id,
    test_class_id,
    test_item_id,
    minor_item = 'Y' AS minor_item,
    rfr_deficiency_category,
    rfr_desc,
    rfr_loc_marker = 'Y' AS rfr_loc_marker,
    rfr_insp_manual_desc,
    rfr_advisory_text,
    test_item_set_section_id
FROM read_csv(
    ${input_file},
    header = true,
    delim = '|',
    quote = '',
    escape = '',
    columns = {
        'rfr_id': 'INTEGER',
        'test_class_id': 'VARCHAR',
        'test_item_id': 'INTEGER',
        'minor_item': 'VARCHAR',
        'rfr_deficiency_category': 'VARCHAR',
        'rfr_desc': 'VARCHAR',
        'rfr_loc_marker': 'VARCHAR',
        'rfr_insp_manual_desc': 'VARCHAR',
        'rfr_advisory_text': 'VARCHAR',
        'test_item_set_section_id': 'INTEGER'
    },
    store_rejects = true,
    rejects_table = 'rej_item_detail',
    rejects_scan = 'rej_item_detail_scan'
)
ORDER BY test_class_id, rfr_id;

COPY lk_item_detail TO ${output_file} (FORMAT parquet, COMPRESSION zstd);

SELECT
    (SELECT count(*) FROM lk_item_detail) AS rows_out,
    (SELECT count(DISTINCT (scan_id, file_id, line)) FROM rej_item_detail) AS rejected_rows;
