from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from spec_runner.errors import RunnerError
from spec_runner.tracker import publish_local, read_local


def issue(key: str, title: str, *, parent: str | None = None, blocked_by: list[str] | None = None) -> str:
    return "\n".join(
        [
            "---",
            f"key: {key}",
            "kind: ticket",
            f"title: {title}",
            "revision: 1",
            f"parent: {parent or 'null'}",
            f"blocked_by: {json.dumps(blocked_by or [])}",
            "comments: []",
            "---",
            f"# {title}",
            "\nbody 内容",
        ]
    )


class TrackerTests(unittest.TestCase):
    def test_read_publish_readback_and_idempotent_retry(self) -> None:
        with tempfile.TemporaryDirectory(prefix="tracker 中文 ") as temporary:
            root = Path(temporary) / "drafts"
            target = Path(temporary) / "published with spaces"
            root.mkdir()
            (root / "first.md").write_text(issue("SR-01", "Spec"), encoding="utf-8")
            (root / "second.md").write_text(issue("SR-01.1", "Ticket", parent="SR-01"), encoding="utf-8")
            snapshot = read_local(root)
            self.assertEqual(snapshot.relation_mode, "local")
            first = publish_local(snapshot, target, operation_id="op-1")
            second = publish_local(snapshot, target, operation_id="op-1")
            self.assertTrue(first["created"])
            self.assertFalse(second["created"])
            reread = read_local(target)
            self.assertEqual(reread.digest, snapshot.digest)

    def test_duplicate_unknown_cycle_and_malicious_frontmatter_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.mkdir(exist_ok=True)
            (root / "a.md").write_text(issue("A", "A", blocked_by=["MISSING"]), encoding="utf-8")
            with self.assertRaisesRegex(RunnerError, "unknown"):
                read_local(root)
            (root / "a.md").write_text(issue("A", "A", blocked_by=["A"]), encoding="utf-8")
            with self.assertRaises(RunnerError):
                read_local(root)
            (root / "a.md").write_text("---\nkey: A\nkind: !!python/object\ntitle: x\nrevision: 1\n---\n", encoding="utf-8")
            with self.assertRaises(RunnerError):
                read_local(root)

    def test_manual_edit_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "drafts"
            target = Path(temporary) / "target"
            root.mkdir()
            (root / "a.md").write_text(issue("A", "A"), encoding="utf-8")
            snapshot = read_local(root)
            publish_local(snapshot, target, operation_id="op-1")
            (target / "A.md").write_text("manual edit", encoding="utf-8")
            with self.assertRaises(RunnerError):
                publish_local(snapshot, target, operation_id="op-2")

    def test_same_operation_replay_rechecks_published_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "drafts"
            target = Path(temporary) / "target"
            root.mkdir()
            (root / "a.md").write_text(issue("A", "A"), encoding="utf-8")
            snapshot = read_local(root)
            publish_local(snapshot, target, operation_id="op-1")
            (target / "A.md").write_text(issue("A", "A") + "edited\n", encoding="utf-8")
            with self.assertRaises(RunnerError) as error:
                publish_local(snapshot, target, operation_id="op-1")
            self.assertEqual(error.exception.code, "tracker_revision_conflict")
            (target / "A.md").unlink()
            with self.assertRaises(RunnerError) as error:
                publish_local(snapshot, target, operation_id="op-1")
            self.assertEqual(error.exception.code, "tracker_revision_conflict")

    def test_corrupt_operation_receipt_cannot_be_used_for_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "drafts"
            target = Path(temporary) / "target"
            root.mkdir()
            (root / "a.md").write_text(issue("A", "A"), encoding="utf-8")
            snapshot = read_local(root)
            publish_local(snapshot, target, operation_id="op-1")
            receipt_path = target / ".spec-runner-tracker-receipts.json"
            receipt_path.write_text(json.dumps({"op-1": {}}), encoding="utf-8")
            with self.assertRaises(RunnerError) as error:
                publish_local(snapshot, target, operation_id="op-1")
            self.assertEqual(error.exception.code, "tracker_receipt_corrupt")

    def test_comment_difference_requires_explicit_matching_revision_adoption(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "drafts"
            target = Path(temporary) / "target"
            root.mkdir()
            (root / "a.md").write_text(issue("A", "A"), encoding="utf-8")
            snapshot = read_local(root)
            target.mkdir()
            (target / "A.md").write_text(
                issue("A", "A").replace("comments: []", 'comments: ["prior-run"]'),
                encoding="utf-8",
            )
            with self.assertRaises(RunnerError) as error:
                publish_local(snapshot, target, operation_id="op-strict")
            self.assertEqual(error.exception.code, "tracker_revision_conflict")
            adopted = publish_local(snapshot, target, operation_id="op-prepared",
                                    adopt_matching_revision=True)
            self.assertTrue(adopted["receipt"]["records"][0]["adopted"])
            replay = publish_local(snapshot, target, operation_id="op-prepared",
                                   adopt_matching_revision=True)
            self.assertFalse(replay["created"])
            self.assertEqual(read_local(target).records[0].comments, ("prior-run",))


if __name__ == "__main__":
    unittest.main()
