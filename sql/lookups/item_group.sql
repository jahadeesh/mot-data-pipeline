-- item_group: the test item hierarchy, one row per (test_item_id, test_class_id).
-- test_item_id 0 is the root ("Vehicle") of each class; parent_id points one level up and
-- test_item_set_section_id points at the top-level group (e.g. "Brakes").
CREATE OR REPLACE TABLE lk_item_group AS
SELECT
    test_item_id,
    test_class_id,
    parent_id,
    test_item_set_section_id,
    item_name
FROM read_csv(
    ${input_file},
    header = true,
    delim = '|',
    quote = '',
    escape = '',
    columns = {
        'test_item_id': 'INTEGER',
        'test_class_id': 'VARCHAR',
        'parent_id': 'INTEGER',
        'test_item_set_section_id': 'INTEGER',
        'item_name': 'VARCHAR'
    },
    store_rejects = true,
    rejects_table = 'rej_item_group',
    rejects_scan = 'rej_item_group_scan'
)
ORDER BY test_class_id, test_item_id;

COPY lk_item_group TO ${output_file} (FORMAT parquet, COMPRESSION zstd);

SELECT
    (SELECT count(*) FROM lk_item_group) AS rows_out,
    (SELECT count(DISTINCT (scan_id, file_id, line)) FROM rej_item_group) AS rejected_rows;
