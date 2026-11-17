# STATIC entry/resource contracts

Run from repository root (Python stdlib only):

```text
python -m unittest discover -s tests -p 'test_*_static.py' -v
```

Public seam: the shipped `to-spec-to-done` entry instructions, discovery metadata and referenced resources. The copy test copies only that selected directory into a temporary directory under `tests/`, then removes the temporary copy. It does not install skills or resolve live upstream dependencies.

These checks detect missing bundled resources, basic frontmatter/link regressions, missing instruction topics and loss of the required unchanged delivery paragraph. Topic regexes allow some wording changes; they are **STATIC text checks**, not a semantic interpreter, a model evaluator, or proof that Pi follows those instructions. Apart from the expressly required delivery paragraph, there are no whole-document snapshots.

Ticket #5 must still exercise native Pi discovery and actual loaded dependencies, unconditional small-task grilling, dependent rounds/new evidence, authorized answer adoption, blocked paths, real single/multi-SPEC publication/readback/native edges, retry deduplication and legacy recovery in an authorized isolated environment. Keep runtime/model/platform evidence separate from these results. No network, Git writes, CC Switch changes, fake Skill API, or scheduler is part of this test suite.
