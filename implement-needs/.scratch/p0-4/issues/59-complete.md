实现与验收证据：

- Commit: `afbe3ce`（独立 observation 流、业务 evidence 引用 API）
- Tests: `test_observations_have_a_separate_cursor_and_do_not_advance_delivery_version`、`test_delivery_proof_and_waiver_are_business_evidence_not_observations`
- Full regression: `python -m unittest discover -s tests -v` → 211 passed
- Acceptance: observations do not advance business version; proof/waiver writes create business events; cross-use is rejected

Acceptance evidence: acceptance:#59
Commit evidence: commit:afbe3ce
Test evidence: test:unittest-211-green

