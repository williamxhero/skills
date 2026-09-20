实现与验收证据：

- Commit: `afbe3ce`（原子业务事务、业务版本、stale-state）
- Tests: `test_event_failure_rolls_back_state_event_and_business_version`、`test_stale_business_version_is_rejected_without_another_event`
- Full regression: `python -m unittest discover -s tests -v` → 211 passed
- Acceptance: explicit transaction rollback and optimistic version rejection verified

Acceptance evidence: acceptance:#58
Commit evidence: commit:afbe3ce
Test evidence: test:unittest-211-green

