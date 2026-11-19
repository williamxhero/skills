"""Supplemental STATIC public resource contract, not native workflow evidence."""

from pathlib import Path
import re
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PrerequisiteDeliveryStaticTests(unittest.TestCase):
    def test_static_b1_requires_shipped_contract_before_selection_or_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            entry = Path(tmp) / "implement-spec-to-done"
            shutil.copytree(ROOT / entry.name, entry)
            document = (entry / "SKILL.md").read_text(encoding="utf-8")
            b1 = document.split("## B1 ", 1)[1].split("## B2 ", 1)[0]
            self.assertRegex(
                b1,
                re.compile(r"选择.*写入.*必须完整读取.*\[.*\]\(references/prerequisite-delivery.md\)", re.S),
                "B1 must require the local prerequisite contract before selection/new writes",
            )
            self.assertTrue((entry / "references/prerequisite-delivery.md").is_file())


if __name__ == "__main__":
    unittest.main()
