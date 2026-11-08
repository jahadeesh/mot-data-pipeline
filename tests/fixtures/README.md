# Test fixtures

Small **synthetic** files in the same shape as the real 2024 DVSA files. Never put real
downloaded data here. `conftest.py` zips them up like the real archives (top folder, monthly
CSVs, `__MACOSX/` junk).

Deliberate quirks, each covered by a test:

| Quirk | Where |
|---|---|
| Exact duplicate test rows (test 1001 ×3) | `test_result_202401.csv` |
| Conflicting duplicate test rows (test 1005: RED vs BLUE) | `test_result_202401.csv` |
| Fuel as full name (`Electric`, `Hybrid Electric (Clean)`) and unknown (`WOOD`) | `test_result_202401.csv` |
| Blank and `0.0` mileage | `test_result_202401.csv` |
| Exact duplicate failure item (test 1002, rfr 100, loc 7) | `test_item_202401.csv` |
| Same RfR at two locations, kept (test 1002, rfr 100, loc 7 and 9) | `test_item_202401.csv` |
| Dangerous mark `D` | `test_item_202401.csv` |
| Embedded double quotes in a pipe-delimited lookup | `lookup/item_detail.csv` |
| A second month, which sample mode must ignore | `*_202402.csv` |
