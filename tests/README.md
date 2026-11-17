# STATIC entry/resource contracts

Run from repository root (Python stdlib only):

```text
python -m unittest discover -s tests -p 'test_*_static.py' -v
```

Public seam: the shipped `to-spec-to-done` and `implement-spec-to-done` entry instructions, discovery metadata and referenced resources. The copy test copies only that selected directory into a temporary directory under `tests/`, then removes the temporary copy. It does not install skills or resolve live upstream dependencies.

These checks detect missing bundled resources, basic frontmatter/link regressions, missing instruction topics and loss of the required unchanged delivery paragraph. Implementation checks additionally verify the selected-directory specialist package against pinned per-file source hashes (52 original files, three main filenames renamed without byte changes, plus licenses), exactly three public manifests, local adapter boundaries and the B1–B12 delivery contract. The implementation directory copy deliberately does not supply the shared sibling: installing the compatible `to-spec-to-done` bundle and separately managed Matt dependencies remains a documented preflight requirement. Topic regexes allow some wording changes; they are **STATIC text checks**, not a semantic interpreter, a model evaluator, or proof that Pi follows those instructions. Apart from the expressly required delivery paragraph, there are no whole-document snapshots.

Ticket #3 red→green logs are outside the worktree in the task execution reports; these are STATIC packaging/text contract evidence only. The specialist directories include their entire selected upstream resources, including inactive optional branches, but only the local adapters' supported modes are invoked. No simplification/final review/model execution is inferred from the suite.

Ticket #5 must still exercise native Pi discovery and actual loaded dependencies, unconditional small-task grilling, dependent rounds/new evidence, authorized answer adoption, blocked paths, real single/multi-SPEC publication/readback/native edges, retry deduplication and legacy recovery in an authorized isolated environment. Keep runtime/model/platform evidence separate from these results. No network, Git writes, CC Switch changes, fake Skill API, or scheduler is part of this test suite.
