实现与验收证据：

- Commit: `afbe3ce`（create/open-existing/read-only 数据库模式与导出入口）
- Tests: `test_read_only_and_open_existing_never_create_missing_database_or_parent`、`test_read_only_existing_database_does_not_change_database_files`、`test_read_only_rejects_incomplete_schema`
- Full regression: `python -m unittest discover -s tests -v` → 211 passed
- Acceptance: missing paths fail without parent creation; immutable read-only access creates no WAL/SHM sidecars; incomplete schema is rejected

Acceptance evidence: acceptance:#60
Commit evidence: commit:afbe3ce
Test evidence: test:unittest-211-green

