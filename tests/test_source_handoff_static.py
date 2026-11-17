"""STATIC source portability and selected-directory closure, not deployment proof."""

import hashlib
from pathlib import Path
import re
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ENTRIES = ("to-spec-to-done", "implement-spec-to-done", "improve-codebase-architecture-to-done")


class SourceHandoffStaticTests(unittest.TestCase):
    def test_static_maintained_sources_have_no_machine_specific_paths(self):
        snapshot = (ROOT / "docs/specs/20261117-pi-engineering-to-done-workflows.md").read_text(encoding="utf-8")
        delivery = snapshot.split("## 执行与交付\n", 1)[1].strip()
        paths = [ROOT / "README.md", ROOT / "AGENTS.md", ROOT / "docs/source-management.md"]
        for name in ENTRIES:
            paths.extend(p for p in (ROOT / name).rglob("*") if p.suffix in {".md", ".yaml"}
                         and "upstream" not in p.parts)
            paths.extend((ROOT / name / "references/upstream").glob("*/adapter.md"))
        for path in paths:
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                document = path.read_text(encoding="utf-8")
                if path == ROOT / "to-spec-to-done/SKILL.md":
                    self.assertEqual(document.count(delivery), 1)
                    document = document.replace(delivery, "")
                self.assertNotRegex(document, r"(?i)[A-Z]:[/\\](?:Users|WILL|Pi)[/\\]")

    def test_static_three_selected_copies_close_maintained_resource_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            staged = Path(tmp)
            for name in ENTRIES:
                shutil.copytree(ROOT / name, staged / name)
            self.assertEqual(sorted(p.relative_to(staged).as_posix() for p in staged.rglob("SKILL.md")),
                             sorted(f"{name}/SKILL.md" for name in ENTRIES))
            documents = []
            for name in ENTRIES:
                documents.extend(p for p in (staged / name).rglob("*.md")
                                 if "upstream" not in p.parts)
                documents.extend((staged / name / "references/upstream").glob("*/adapter.md"))
            for path in documents:
                for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
                    if ":" in target or target.startswith("/"):
                        continue
                    with self.subTest(document=path.relative_to(staged).as_posix(), target=target):
                        resolved = (path.parent / target.split("#", 1)[0]).resolve()
                        self.assertTrue(resolved.is_relative_to(staged.resolve()))
                        self.assertTrue(resolved.is_file())
            # Original to-spec is separately managed, never another bundled template.
            self.assertFalse((staged / "to-spec").exists())
            for path in documents:
                self.assertNotIn("<spec-template>", path.read_text(encoding="utf-8"))
            shared = staged / "to-spec-to-done/references/engineering-quality.md"
            shared.unlink()
            for name in ENTRIES[1:]:
                self.assertFalse((staged / name / "../to-spec-to-done/references/engineering-quality.md").is_file())

    def test_static_historical_export_snapshot_remains_exact(self):
        self.assertEqual(hashlib.sha256((ROOT / "docs/source-snapshot.json").read_bytes()).hexdigest(),
                         "f2a06c3eceb6bf9cfb012e2cce7704739b6ce5ce8883571594d05c516c0de1e7")


if __name__ == "__main__":
    unittest.main()
