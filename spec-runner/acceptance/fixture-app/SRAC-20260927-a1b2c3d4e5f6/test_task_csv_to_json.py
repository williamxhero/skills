import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parent
COMMAND = ROOT / "task_csv_to_json.py"
TEST_ROOT = ROOT / ".test-data"


class TaskCsvToJsonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(self.clean_test_data)

    def clean_test_data(self) -> None:
        for path in TEST_ROOT.iterdir():
            if path.name == ".gitkeep":
                continue
            if path.is_file() or path.is_symlink():
                path.unlink()

    def run_command(
        self, input_path: Path, output_path: Path, *extra: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(COMMAND), str(input_path), str(output_path), *extra],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )

    def write_input(self, directory: Path, content: str | bytes) -> Path:
        path = directory / "input.csv"
        if isinstance(content, str):
            path.write_text(content, encoding="utf-8", newline="")
        else:
            path.write_bytes(content)
        return path

    def test_valid_csv_is_deterministic_and_normalized(self) -> None:
        directory = TEST_ROOT
        input_path = self.write_input(
            directory,
            "id,title,status\n"
            " 1 ,\"A, task\",queued\n"
            "1,\"Say \"\"hello\"\"\", in progress\n"
            "3,\"line one\nline two\",done\n",
        )
        output_path = directory / "output.json"

        first = self.run_command(input_path, output_path)
        first_bytes = output_path.read_bytes()
        second = self.run_command(input_path, output_path)

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first_bytes, output_path.read_bytes())
        self.assertEqual(
            first_bytes,
            (
                '[\n'
                '  {\n'
                '    "id": "1",\n'
                '    "title": "A, task",\n'
                '    "status": "queued"\n'
                '  },\n'
                '  {\n'
                '    "id": "1",\n'
                '    "title": "Say \\"hello\\"",\n'
                '    "status": "in progress"\n'
                '  },\n'
                '  {\n'
                '    "id": "3",\n'
                '    "title": "line one\\nline two",\n'
                '    "status": "done"\n'
                '  }\n'
                ']\n'
            ).encode("utf-8"),
        )
        self.assertEqual(json.loads(first_bytes), [
            {"id": "1", "title": "A, task", "status": "queued"},
            {"id": "1", "title": 'Say "hello"', "status": "in progress"},
            {"id": "3", "title": "line one\nline two", "status": "done"},
        ])

    def test_bom_unicode_and_header_only_input(self) -> None:
        directory = TEST_ROOT
        input_path = self.write_input(
            directory, b"\xef\xbb\xbfid,title,status\n7,\xe4\xbb\xbb\xe5\x8a\xa1,\xe5\xae\x8c\xe6\x88\x90\n"
        )
        output_path = directory / "output.json"

        result = self.run_command(input_path, output_path)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            output_path.read_bytes(),
            '[\n  {\n    "id": "7",\n    "title": "任务",\n    "status": "完成"\n  }\n]\n'.encode(
                "utf-8"
            ),
        )

        header_only = self.write_input(directory, "id,title,status\n")
        header_output = directory / "header.json"
        result = self.run_command(header_only, header_output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(header_output.read_bytes(), b"[]\n")

    def test_failures_preserve_destination_and_leave_no_temporary_files(self) -> None:
        cases = [
            ("empty", b"", "validation error"),
            ("bad-header", "id,title, status\n1,todo,open\n", "header"),
            ("bad-width", "id,title,status\n1,todo\n", "expected 3 fields"),
            ("blank-field", "id,title,status\n1,  ,open\n", "column 2"),
            ("malformed", 'id,title,status\n1,"unterminated,open\n', "CSV syntax error"),
            ("bad-encoding", b"id,title,status\n1,\xff,open\n", "input encoding error"),
        ]

        for name, content, diagnostic in cases:
            with self.subTest(name=name):
                directory = TEST_ROOT
                input_path = self.write_input(directory, content)
                output_path = directory / "output.json"
                sentinel = b"keep this destination"
                output_path.write_bytes(sentinel)

                result = self.run_command(input_path, output_path)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(diagnostic, result.stderr)
                self.assertEqual(output_path.read_bytes(), sentinel)
                self.assertEqual(list(directory.glob(".output.json.*.tmp")), [])

    def test_cli_and_filesystem_contract(self) -> None:
        directory = TEST_ROOT
        input_path = self.write_input(directory, "id,title,status\n1,todo,open\n")
        output_path = directory / "output.json"

        missing = subprocess.run(
            [sys.executable, str(COMMAND)],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("usage:", missing.stderr)

        extra = self.run_command(input_path, output_path, "extra")
        self.assertNotEqual(extra.returncode, 0)
        self.assertIn("usage:", extra.stderr)

        same = self.run_command(input_path, input_path)
        self.assertNotEqual(same.returncode, 0)
        self.assertIn("different", same.stderr)
        self.assertEqual(input_path.read_text(encoding="utf-8"), "id,title,status\n1,todo,open\n")

        missing_parent = directory / "missing" / "output.json"
        result = self.run_command(input_path, missing_parent)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("parent directory", result.stderr)
        self.assertFalse(missing_parent.parent.exists())

    def test_records_with_duplicate_ids_and_arbitrary_statuses_are_accepted(self) -> None:
        directory = TEST_ROOT
        input_path = self.write_input(
            directory,
            "id,title,status\nsame,one,any status\nsame,two,42\n",
        )
        output_path = directory / "output.json"

        result = self.run_command(input_path, output_path)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(output_path.read_text(encoding="utf-8")),
            [
                {"id": "same", "title": "one", "status": "any status"},
                {"id": "same", "title": "two", "status": "42"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
