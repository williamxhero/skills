"""Task identity, recovery-index titles, and registry binding tests."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from control_db import SCHEMA_VERSION, ControlDB
from task_binding import (
    AMBIGUOUS,
    BOUND,
    MISMATCH,
    UNRESOLVED,
    UNTRACKED,
    Candidate,
    bind_candidate,
    bind_candidates,
    may_advance_attempt,
)
from task_identity import (
    DESCRIPTION_TOO_LONG,
    INVALID_ATTEMPT,
    MALFORMED,
    MISSING,
    UNSUPPORTED_VERSION,
    TaskIdentity,
    format_title,
    format_token,
    mint_nonce,
    next_attempt_id,
    parse_title,
    resolve_candidates,
    token_matches,
)

NONCE = "0123456789abcdef"


def identity(task="T1", run="run-1", attempt="01", nonce=NONCE):
    return TaskIdentity(task, run, attempt, nonce)


class TitleTokenTests(unittest.TestCase):
    def test_round_trip_preserves_identity_and_description(self):
        original = identity()
        title = format_title(original, "implement Issue 444")
        parsed = parse_title(title)
        self.assertTrue(parsed.ok)
        self.assertEqual(original, parsed.identity)
        self.assertEqual("implement Issue 444", parsed.description)
        self.assertTrue(token_matches(original, parsed))

    def test_token_shape_is_stable_and_fixed_order(self):
        self.assertEqual(
            "[INN v=1 task=T1 run=run-1 attempt=01 nonce=0123456789abcdef]",
            format_token(identity()),
        )
        self.assertTrue(format_title(identity(), "x").startswith(format_token(identity())))

    def test_description_whitespace_collapses_to_one_title_line(self):
        title = format_title(identity(), "  implement   Issue\n444  ")
        self.assertEqual("implement Issue 444", parse_title(title).description)

    def test_missing_token_is_reported_not_guessed(self):
        for title in (None, "", "   ", "implement Issue 444"):
            with self.subTest(title=title):
                parsed = parse_title(title)
                self.assertFalse(parsed.ok)
                self.assertIsNone(parsed.identity)
                self.assertIn(MISSING, parsed.errors)

    def test_unsupported_version_is_rejected(self):
        parsed = parse_title(
            "[INN v=2 task=T1 run=run-1 attempt=01 nonce=0123456789abcdef] x"
        )
        self.assertFalse(parsed.ok)
        self.assertEqual((UNSUPPORTED_VERSION,), parsed.errors)

    def test_edited_or_reordered_token_is_malformed(self):
        edited = (
            "[INN v=1 run=run-1 task=T1 attempt=01 nonce=0123456789abcdef] x",
            "[INN v=1 task=T1 run=run-1 attempt=01] x",
            "[INN v=1 task=T1 run=run-1 attempt=01 nonce=0123456789ABCDEF] x",
            "[INN v=1 task=T1 run=run-1 attempt=01 nonce=0123456789abcde] x",
        )
        for title in edited:
            with self.subTest(title=title):
                parsed = parse_title(title)
                self.assertFalse(parsed.ok)
                self.assertIsNone(parsed.identity)
                self.assertIn(parsed.errors[0], {MALFORMED, INVALID_ATTEMPT})

    def test_truncated_token_is_malformed(self):
        full = format_token(identity())
        for cut in (len(full) - 1, len(full) - 5, len(full) // 2):
            with self.subTest(cut=cut):
                parsed = parse_title(full[:cut])
                self.assertFalse(parsed.ok)
                self.assertIsNone(parsed.identity)
                self.assertIn(MALFORMED, parsed.errors)

    def test_forged_token_with_wrong_nonce_does_not_match(self):
        forged = TaskIdentity("T1", "run-1", "01", "ffffffffffffffff")
        parsed = parse_title(format_title(forged, "spoofed attempt"))
        self.assertTrue(parsed.ok)
        self.assertFalse(token_matches(identity(), parsed))

    def test_attempt_id_must_be_zero_padded_pair_or_more(self):
        with self.assertRaises(ValueError):
            format_token(identity(attempt="3"))
        for bad in ("1", "abc", ""):
                with self.subTest(attempt=bad), self.assertRaises(ValueError):
                    format_token(identity(attempt=bad))
        self.assertEqual(
            "100",
            format_token(identity(attempt="100")).split("attempt=")[1].split(" ")[0],
        )

    def test_empty_description_is_an_error_even_with_valid_token(self):
        parsed = parse_title(format_token(identity()))
        self.assertIsNotNone(parsed.identity)
        self.assertFalse(parsed.ok)
        self.assertIn("title_token_empty_description", parsed.errors)

    def test_description_is_length_bounded(self):
        with self.assertRaises(ValueError):
            format_title(identity(), "x" * 401)
        parsed = parse_title(format_token(identity()) + " " + "y" * 401)
        self.assertIn(DESCRIPTION_TOO_LONG, parsed.errors)

    def test_next_attempt_id_pads_and_advances(self):
        self.assertEqual("01", next_attempt_id(None))
        self.assertEqual("02", next_attempt_id("01"))
        self.assertEqual("04", next_attempt_id("03"))
        self.assertEqual("100", next_attempt_id("99"))
        with self.assertRaises(ValueError):
            next_attempt_id("3")

    def test_nonce_is_unique_and_well_formed(self):
        minted = {mint_nonce() for _ in range(64)}
        self.assertEqual(64, len(minted))
        for nonce in minted:
            self.assertEqual(16, len(nonce))
            self.assertRegex(nonce, r"^[0-9a-f]{16}$")


class CandidateResolutionTests(unittest.TestCase):
    def test_single_match_resolves(self):
        titles = ["unrelated", format_title(identity(), "target"), "noise"]
        matches, defects = resolve_candidates(identity(), titles)
        self.assertEqual([1], matches)
        self.assertIn(MISSING, defects)

    def test_duplicate_tokens_are_ambiguous(self):
        title = format_title(identity(), "same attempt")
        matches, _ = resolve_candidates(identity(), [title, title])
        self.assertEqual([0, 1], matches)

    def test_edited_title_loses_the_candidate(self):
        edited = format_title(identity(), "target").replace("attempt=01", "attempt=02")
        matches, defects = resolve_candidates(identity(), [edited])
        self.assertEqual([], matches)
        self.assertEqual(["identity_mismatch"], defects)


class BindingTests(unittest.TestCase):
    def registered(self, **overrides):
        row = {
            "task_id": "T1",
            "run_id": "run-1",
            "attempt_id": "01",
            "owner": "owner-1",
            "cwd": "/repo",
            "project_id": "skills",
        }
        row.update(overrides)
        return row

    def candidate(self, **overrides):
        values = {
            "formal_thread_id": "th-1",
            "host_id": "host-1",
            "title": format_title(identity(), "implement Issue 444"),
            "task_id": "T1",
            "run_id": "run-1",
            "attempt_id": "01",
            "owner_id": "owner-1",
            "cwd": "/repo",
            "project_id": "skills",
            "lifecycle": "active",
            "readback_evidence": ("thread/read",),
        }
        values.update(overrides)
        return Candidate(**values)

    def test_matching_readback_binds(self):
        decision = bind_candidate(self.registered(), self.candidate())
        self.assertTrue(decision.may_bind)
        self.assertEqual(BOUND, decision.status)
        self.assertFalse(decision.blocks_creation)

    def test_partial_readback_never_binds(self):
        for missing in ("formal_thread_id", "host_id", "task_id", "run_id", "attempt_id", "owner_id", "cwd", "project_id"):
            with self.subTest(missing=missing):
                decision = bind_candidate(
                    self.registered(), self.candidate(**{missing: None})
                )
                self.assertFalse(decision.may_bind)
                self.assertEqual(UNRESOLVED, decision.status)
                self.assertIn(missing, decision.mismatched_fields)
                self.assertTrue(decision.blocks_creation)

    def test_each_disagreeing_field_blocks_binding(self):
        wrong = {
            "run_id": "run-2",
            "attempt_id": "02",
            "cwd": "/other",
            "project_id": "other",
            "owner_id": "owner-2",
        }
        for field, value in wrong.items():
            with self.subTest(field=field):
                decision = bind_candidate(self.registered(), self.candidate(**{field: value}))
                self.assertFalse(decision.may_bind)
                self.assertEqual(MISMATCH, decision.status)
                self.assertIn(field, decision.mismatched_fields)

    def test_missing_readback_evidence_blocks_binding(self):
        decision = bind_candidate(
            self.registered(), self.candidate(readback_evidence=())
        )
        self.assertFalse(decision.may_bind)
        self.assertEqual(UNRESOLVED, decision.status)

    def test_legacy_row_is_untracked_and_excluded_from_gating(self):
        decision = bind_candidate(
            self.registered(task_id=None, attempt_id=None), self.candidate()
        )
        self.assertEqual(UNTRACKED, decision.status)
        self.assertFalse(decision.may_bind)
        self.assertFalse(decision.blocks_creation)

    def test_multiple_candidates_are_ambiguous(self):
        decision = bind_candidates(
            self.registered(), [self.candidate(), self.candidate(formal_thread_id="th-2")]
        )
        self.assertEqual(AMBIGUOUS, decision.status)
        self.assertFalse(decision.may_bind)
        self.assertTrue(decision.blocks_creation)

    def test_no_candidate_is_unresolved(self):
        decision = bind_candidates(self.registered(), [])
        self.assertEqual(UNRESOLVED, decision.status)
        self.assertTrue(decision.blocks_creation)

    def test_attempt_advances_only_after_terminal_outcome(self):
        self.assertFalse(may_advance_attempt("created", "working"))
        self.assertFalse(may_advance_attempt("working", "unknown"))
        self.assertTrue(may_advance_attempt("archived", "unknown"))
        self.assertTrue(may_advance_attempt("verified", "unknown"))
        self.assertTrue(may_advance_attempt("working", "abandoned_after_bootstrap"))


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.db = ControlDB(Path(self.temporary.name) / "control.db")
        self.db.create_run("r1", "initiative", "requirement")
        self.db.add_spec("r1", "S1", "spec one", 1)
        self.db.add_spec("r1", "S2", "spec two", 2)

    def tearDown(self):
        self.db.close()
        self.temporary.cleanup()

    def test_repeat_registration_for_one_attempt_is_refused(self):
        first = self.db.add_thread("r1", "th-1", "spec", "S1", identity=identity())
        second = self.db.add_thread("r1", "th-2", "spec", "S1", identity=identity())
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(
            ["th-1"], [row["thread_id"] for row in self.db.threads_by_identity("r1", "T1", "01")]
        )

    def test_distinct_attempts_are_separate_rows(self):
        self.assertTrue(self.db.add_thread("r1", "th-1", "spec", "S1", identity=identity(attempt="01")))
        self.assertTrue(self.db.add_thread("r1", "th-2", "spec", "S1", identity=identity(attempt="02")))
        rows = self.db.threads_by_identity("r1", "T1")
        self.assertEqual(["01", "02"], [row["attempt_id"] for row in rows])

    def test_legacy_row_without_identity_is_untracked(self):
        self.assertTrue(self.db.add_thread("r1", "th-legacy", "spec", "S2"))
        self.assertEqual([], self.db.threads_by_title_token(format_token(identity())))
        self.assertEqual(
            ["th-legacy"], [row["thread_id"] for row in self.db.untracked_threads("r1")]
        )

    def test_bind_identity_records_formal_id_and_host(self):
        self.db.add_thread("r1", "th-1", "spec", "S1", identity=identity())
        self.db.bind_identity("r1", "th-1", "formal-1", "host-1", identity_readback="{}")
        rows = self.db.threads_by_formal_id("formal-1", "host-1")
        self.assertEqual(["th-1"], [row["thread_id"] for row in rows])
        self.assertEqual([], self.db.threads_by_formal_id("formal-1", "host-2"))

    def test_bind_identity_rejects_unknown_thread(self):
        with self.assertRaises(ValueError):
            self.db.bind_identity("r1", "missing", "formal-1", "host-1")

    def test_route_evidence_round_trips(self):
        self.db.add_thread("r1", "th-1", "spec", "S1", identity=identity())
        self.db.set_route_evidence("r1", "th-1", route_readback="{readback}", route_receipt="{receipt}")
        row = self.db.threads_by_identity("r1", "T1", "01")[0]
        self.assertEqual("{readback}", row["route_readback"])
        self.assertEqual("{receipt}", row["route_receipt"])

    def test_title_token_lookup_finds_the_registered_attempt(self):
        token = format_token(identity())
        self.db.add_thread("r1", "th-1", "spec", "S1", identity=identity(), title_token=token)
        self.assertEqual(
            ["th-1"], [row["thread_id"] for row in self.db.threads_by_title_token(token)]
        )

    def test_attempt_advance_requires_terminal_thread(self):
        self.db.add_thread("r1", "th-1", "spec", "S1", identity=identity())
        with self.assertRaises(ValueError):
            self.db.next_attempt_id("r1", "T1", "01")
        version = self.db.business_version("r1")
        gate = {
            "schema_version": 1,
            "expected": {"run_id": "r1", "target_id": "th-1", "candidate_sha": "abc", "environment": "test", "business_version": version},
            "actor": {"id": "owner", "authorized": True},
            "source": {"kind": "independent-readback", "trust": "verified"},
            "readback": {"status": "verified", "run_id": "r1", "target_id": "th-1", "candidate_sha": "abc", "environment": "test", "archived": True},
            "archive_operation": True,
            "archive_readback": True,
        }
        self.db.update_thread("r1", "th-1", "archived", outcome="completed", operation="archive-op", readback="archive-readback", gate=gate)
        self.assertEqual("02", self.db.next_attempt_id("r1", "T1", "01"))


class MigrationTests(unittest.TestCase):
    def test_version_one_database_migrates_and_keeps_audit_rows(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "legacy.db"
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            CREATE TABLE runs(run_id TEXT PRIMARY KEY, initiative TEXT NOT NULL, requirement TEXT NOT NULL, status TEXT NOT NULL, current_action TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE threads(thread_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), kind TEXT NOT NULL, spec_id TEXT, lifecycle TEXT NOT NULL, outcome TEXT NOT NULL DEFAULT 'unknown', next_action TEXT, last_observed_at TEXT NOT NULL, archive_operation_evidence TEXT NOT NULL DEFAULT '[]', archive_readback_evidence TEXT NOT NULL DEFAULT '[]');
            """
        )
        connection.execute(
            "INSERT INTO runs VALUES(?,?,?,?,?,?,?)", ("r9", "i", "q", "active", None, "t", "t")
        )
        connection.execute(
            "INSERT INTO threads(thread_id,run_id,kind,lifecycle,last_observed_at) VALUES(?,?,?,?,?)",
            ("old-1", "r9", "spec", "created", "t"),
        )
        connection.commit()
        connection.close()

        db = ControlDB(path)
        self.addCleanup(db.close)
        self.assertEqual(
            SCHEMA_VERSION,
            db.conn.execute("SELECT version FROM schema_meta").fetchone()[0],
        )
        kept = [dict(row) for row in db.conn.execute("SELECT thread_id, task_id FROM threads")]
        self.assertEqual([{"thread_id": "old-1", "task_id": None}], kept)
        self.assertEqual(["old-1"], [row["thread_id"] for row in db.untracked_threads("r9")])
        columns = {row[1] for row in db.conn.execute("PRAGMA table_info(threads)")}
        self.assertIn("formal_thread_id", columns)
        self.assertIn("title_token", columns)
        self.assertNotIn("run_identity", columns)

    def test_newer_database_schema_is_refused(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "future.db"
        db = ControlDB(path)
        db.conn.execute(
            "UPDATE schema_meta SET version=?", (SCHEMA_VERSION + 1,)
        )
        db.close()
        with self.assertRaises(ValueError):
            ControlDB(path)


if __name__ == "__main__":
    unittest.main()
