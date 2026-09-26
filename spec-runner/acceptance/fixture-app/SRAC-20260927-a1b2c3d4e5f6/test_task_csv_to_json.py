import json
from pathlib import Path
import subprocess
import sys
import textwrap
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
            ("reordered-header", "title,id,status\n1,todo,open\n", "header"),
            ("duplicated-header", "id,id,status\n1,todo,open\n", "header"),
            ("missing-header", "id,title\n1,todo\n", "header"),
            ("extra-header", "id,title,status,owner\n1,todo,open,me\n", "header"),
            ("bad-width", "id,title,status\n1,todo\n", "expected 3 fields"),
            ("extra-column", "id,title,status\n1,todo,open,extra\n", "expected 3 fields"),
            ("blank-field", "id,title,status\n1,  ,open\n", "column 2"),
            ("blank-row", "id,title,status\n\n", "blank data row"),
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

    def test_all_data_fields_are_trimmed(self) -> None:
        input_path = self.write_input(
            TEST_ROOT,
            "id,title,status\n  task-1  ,  Needs review  ,  waiting  \n",
        )
        output_path = TEST_ROOT / "output.json"

        result = self.run_command(input_path, output_path)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(output_path.read_text(encoding="utf-8")),
            [{"id": "task-1", "title": "Needs review", "status": "waiting"}],
        )

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

    def test_same_file_aliases_are_rejected_before_writing(self) -> None:
        input_path = self.write_input(
            TEST_ROOT, "id,title,status\n1,todo,open\n"
        )
        original = input_path.read_bytes()

        hardlink_path = TEST_ROOT / "hardlink.csv"
        try:
            hardlink_path.hardlink_to(input_path)
        except (FileExistsError, NotImplementedError, OSError) as exc:
            self.skipTest(f"hard links unavailable: {exc}")

        hardlink_result = self.run_command(input_path, hardlink_path)
        self.assertNotEqual(hardlink_result.returncode, 0)
        self.assertIn("different", hardlink_result.stderr)
        self.assertEqual(input_path.read_bytes(), original)

        symlink_path = TEST_ROOT / "symlink.csv"
        try:
            symlink_path.symlink_to(input_path)
        except (FileExistsError, NotImplementedError, OSError) as exc:
            self.skipTest(f"symbolic links unavailable: {exc}")

        symlink_result = self.run_command(input_path, symlink_path)
        self.assertNotEqual(symlink_result.returncode, 0)
        self.assertIn("different", symlink_result.stderr)
        self.assertEqual(input_path.read_bytes(), original)

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

    def test_atomic_replacement_changes_destination_only_after_success(self) -> None:
        input_path = self.write_input(
            TEST_ROOT, "id,title,status\n1,new,complete\n"
        )
        output_path = TEST_ROOT / "output.json"
        output_path.write_bytes(b"old complete destination")

        before = output_path.stat()
        result = self.run_command(input_path, output_path)

        self.assertEqual(result.returncode, 0, result.stderr)
        after = output_path.stat()
        self.assertNotEqual(before.st_ino, after.st_ino)
        self.assertEqual(
            output_path.read_bytes(),
            b'[\n  {\n    "id": "1",\n    "title": "new",\n    "status": "complete"\n  }\n]\n',
        )
        self.assertEqual(list(TEST_ROOT.glob(".output.json.*.tmp")), [])

    def test_write_flush_and_replace_failures_preserve_destination_and_cleanup(self) -> None:
        helper = textwrap.dedent(
            f"""
            import os
            import sys
            from pathlib import Path
            from unittest.mock import patch

            sys.path.insert(0, {str(ROOT)!r})
            import task_csv_to_json

            output = Path(sys.argv[1])
            payload = b"new payload"

            class BaseFile:
                name = str(output.parent / ".output.json.injected.tmp")

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def fileno(self):
                    return 1

            class WriteFailingFile(BaseFile):
                def write(self, value):
                    raise OSError("injected write failure")

                def flush(self):
                    raise AssertionError("flush must not be reached after write failure")

            class FlushFailingFile(BaseFile):
                def write(self, value):
                    return len(value)

                def flush(self):
                    raise OSError("injected flush failure")

            def expect_failure(named_temporary_file, expected_message):
                with patch.object(
                    task_csv_to_json.tempfile,
                    "NamedTemporaryFile",
                    **named_temporary_file,
                ):
                    try:
                        task_csv_to_json.install_atomically(output, payload)
                    except task_csv_to_json.ConversionError as error:
                        if expected_message not in str(error):
                            raise AssertionError(str(error))
                    else:
                        raise AssertionError("injected failure was not reported")

            expect_failure(
                {{"return_value": WriteFailingFile()}},
                "output I/O error",
            )
            expect_failure(
                {{"return_value": FlushFailingFile()}},
                "output I/O error",
            )
            expect_failure(
                {{"side_effect": OSError("injected temp creation failure")}},
                "output I/O error",
            )

            def failing_replace(*args):
                raise OSError("injected replace failure")

            with patch.object(task_csv_to_json.os, "replace", failing_replace):
                try:
                    task_csv_to_json.install_atomically(output, payload)
                except task_csv_to_json.ConversionError:
                    pass
                else:
                    raise AssertionError("replace failure was not reported")
            """
        )
        helper_path = TEST_ROOT / "failure_helper.py"
        helper_path.write_text(helper, encoding="utf-8", newline="")
        output_path = TEST_ROOT / "output.json"
        sentinel = b"old complete destination"
        output_path.write_bytes(sentinel)

        result = subprocess.run(
            [sys.executable, str(helper_path), str(output_path)],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output_path.read_bytes(), sentinel)
        self.assertEqual(list(TEST_ROOT.glob(".output.json.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
