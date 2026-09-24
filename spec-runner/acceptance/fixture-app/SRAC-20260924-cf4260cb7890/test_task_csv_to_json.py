import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import task_csv_to_json


SCRIPT = Path(__file__).with_name("task_csv_to_json.py")


class TaskCsvToJsonTests(unittest.TestCase):
    def run_command(self, fixture):
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(SCRIPT.parent / "testdata" / fixture)],
            capture_output=True,
        )

    def test_valid_csv_is_compact_ordered_json(self):
        result = self.run_command("valid.csv")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            result.stdout.decode("utf-8"),
            '[{"id":"first","title":"First task","status":"pending"},'
            '{"id":"second","title":"Title, with \\"quotes\\"","status":"done"}]',
        )
        self.assertEqual(result.stderr, b"")

    def test_bom_embedded_newline_and_header_only(self):
        result = self.run_command("bom_multiline.csv")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            json.loads(result.stdout),
            [{"id": "one", "title": "line one\nline two", "status": "in_progress"}],
        )

        empty = self.run_command("header_only.csv")
        self.assertEqual((empty.returncode, empty.stdout, empty.stderr), (0, b"[]", b""))

    def test_non_ascii_output_is_utf8_with_non_utf8_stdout_encoding(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            input_path = Path(temporary_directory) / "unicode.csv"
            input_path.write_text("id,title,status\n1,雪,pending\n", encoding="utf-8")
            environment = os.environ.copy()
            environment["PYTHONIOENCODING"] = "cp1252"
            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(input_path)],
                capture_output=True,
                env=environment,
            )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            result.stdout,
            '[{"id":"1","title":"雪","status":"pending"}]'.encode("utf-8"),
        )
        self.assertEqual(result.stderr, b"")

    def test_failures_are_exit_two_with_no_stdout(self):
        invalid_fixtures = [
            "empty.csv", "blank_row.csv", "bad_status.csv", "empty_field.csv",
            "duplicate_id.csv", "missing_column.csv", "reordered_header.csv",
            "extra_column.csv", "malformed.csv", "partial_before_error.csv",
        ]
        for fixture in invalid_fixtures:
            with self.subTest(fixture=fixture):
                result = self.run_command(fixture)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b"")
                self.assertTrue(result.stderr)

    def test_usage_and_missing_file_fail_without_stdout(self):
        no_arguments = subprocess.run(
            [sys.executable, str(SCRIPT)], capture_output=True
        )
        self.assertEqual(no_arguments.returncode, 2)
        self.assertEqual(no_arguments.stdout, b"")
        self.assertTrue(no_arguments.stderr)

        extra_arguments = subprocess.run(
            [sys.executable, str(SCRIPT), "one.csv", "two.csv"], capture_output=True
        )
        self.assertEqual(extra_arguments.returncode, 2)
        self.assertEqual(extra_arguments.stdout, b"")
        self.assertTrue(extra_arguments.stderr)

        missing = subprocess.run(
            [sys.executable, str(SCRIPT), str(SCRIPT.parent / "testdata" / "missing.csv")], capture_output=True
        )
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(missing.stdout, b"")
        self.assertTrue(missing.stderr)

    def test_permission_denied_fails_without_stdout(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(Path, "open", side_effect=PermissionError("access denied")),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            exit_code = task_csv_to_json.main(["unreadable.csv"])

        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("cannot read input file", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
