from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import task_converter


class ConversionTests(unittest.TestCase):
    def test_preserves_order_values_and_sorted_exact_status_counts(self) -> None:
        result = task_converter.convert_csv(
            io.StringIO(
                'status,extra,title,id\r\n'
                'Open,ignored," second, task ",2\r\n'
                ' open ,metadata,first,1\r\n'
                'Open,ignored,third,3\r\n'
            )
        )

        self.assertEqual(
            result,
            {
                "rows": [
                    {"id": "2", "title": " second, task ", "status": "Open"},
                    {"id": "1", "title": "first", "status": " open "},
                    {"id": "3", "title": "third", "status": "Open"},
                ],
                "counts": {" open ": 1, "Open": 2},
            },
        )
        self.assertEqual(list(result["counts"]), sorted(result["counts"]))

    def test_accepts_header_only_and_rejects_invalid_inputs(self) -> None:
        self.assertEqual(
            task_converter.convert_csv("title,id,status\n"),
            {"rows": [], "counts": {}},
        )
        cases = (
            "id,title\n1,task\n",
            "id,title,status,id\n1,task,open,2\n",
            "id,title,status\n\n",
            "id,title,status\n1,   ,open\n",
            "id,title,status\n1,task,open\n1,other,done\n",
            'id,title,status\n1,"unterminated,open\n',
            'id,title,status\n1,bad"quote,open\n',
            "id,title,status\n1,task\n",
        )
        for csv_text in cases:
            with self.subTest(csv_text=csv_text):
                with self.assertRaises(task_converter.ValidationError):
                    task_converter.convert_csv(csv_text)

    def test_rejects_each_missing_or_duplicate_required_header(self) -> None:
        for missing in task_converter.REQUIRED_HEADERS:
            headers = [name for name in task_converter.REQUIRED_HEADERS if name != missing]
            with self.subTest(missing=missing):
                with self.assertRaises(task_converter.ValidationError):
                    task_converter.convert_csv(",".join(headers) + "\n")

        for duplicate in task_converter.REQUIRED_HEADERS:
            headers = [*task_converter.REQUIRED_HEADERS, duplicate]
            with self.subTest(duplicate=duplicate):
                with self.assertRaises(task_converter.ValidationError):
                    task_converter.convert_csv(",".join(headers) + "\n")

    def test_rejects_blank_values_for_each_required_field(self) -> None:
        for index, field in enumerate(task_converter.REQUIRED_HEADERS):
            for blank in ("", "   "):
                values = ["1", "task", "open"]
                values[index] = blank
                csv_text = ",".join(task_converter.REQUIRED_HEADERS) + "\n"
                csv_text += ",".join(values) + "\n"
                with self.subTest(field=field, blank=repr(blank)):
                    with self.assertRaises(task_converter.ValidationError):
                        task_converter.convert_csv(csv_text)


class DestinationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = self._new_path(".csv-test-")
        self.output = self._new_path(".json-test-")

    def tearDown(self) -> None:
        self.source.unlink(missing_ok=True)
        self.output.unlink(missing_ok=True)
        for path in Path.cwd().glob(f".{self.output.name}.*.tmp"):
            path.unlink(missing_ok=True)

    @staticmethod
    def _new_path(prefix: str) -> Path:
        descriptor, name = tempfile.mkstemp(prefix=prefix, dir=Path.cwd())
        os.close(descriptor)
        path = Path(name)
        path.unlink()
        return path

    def test_deterministic_atomic_success_and_failure_safety(self) -> None:
        self.source.write_text("id,title,status\n1,café,open\n", encoding="utf-8")
        self.output.write_bytes(b"old bytes")

        task_converter.convert_csv_to_json(self.source, self.output)
        first = self.output.read_bytes()
        task_converter.convert_csv_to_json(self.source, self.output)

        self.assertEqual(self.output.read_bytes(), first)
        self.assertEqual(json.loads(first)["counts"], {"open": 1})
        self.assertEqual(list(Path.cwd().glob(f".{self.output.name}.*.tmp")), [])

        with mock.patch.object(os, "replace", side_effect=OSError("replace failed")):
            with self.assertRaises(task_converter.ValidationError):
                task_converter.write_result({"rows": [], "counts": {}}, self.output)
        self.assertEqual(self.output.read_bytes(), first)
        self.assertEqual(list(Path.cwd().glob(f".{self.output.name}.*.tmp")), [])

    def test_serialization_and_fsync_failures_preserve_destination(self) -> None:
        self.source.write_text("id,title,status\n1,task,open\n", encoding="utf-8")
        self.output.write_bytes(b"keep exactly")

        with mock.patch.object(
            task_converter,
            "_serialize",
            side_effect=task_converter.ValidationError("serialize failed"),
        ):
            with self.assertRaises(task_converter.ValidationError):
                task_converter.write_result({"rows": [], "counts": {}}, self.output)
        self.assertEqual(self.output.read_bytes(), b"keep exactly")

        with mock.patch.object(os, "fsync", side_effect=OSError("fsync failed")):
            with self.assertRaises(task_converter.ValidationError):
                task_converter.write_result({"rows": [], "counts": {}}, self.output)
        self.assertEqual(self.output.read_bytes(), b"keep exactly")

    def test_missing_output_directory_is_not_created(self) -> None:
        self.source.write_text("id,title,status\n1,task,open\n", encoding="utf-8")
        missing = Path.cwd() / f"missing-{self.source.name}"

        with self.assertRaises(task_converter.ValidationError):
            task_converter.convert_csv_to_json(self.source, missing / "tasks.json")
        self.assertFalse(missing.exists())

    def test_cli_uses_exit_two_and_one_line_stderr_on_failure(self) -> None:
        self.source.write_text("id,title,status\n1,,open\n", encoding="utf-8")
        self.output.write_bytes(b"keep exactly")

        completed = subprocess.run(
            [
                sys.executable,
                str(Path(task_converter.__file__)),
                "--input",
                str(self.source),
                "--output",
                str(self.output),
            ],
            check=False,
            capture_output=True,
            text=True,
            cwd=Path(task_converter.__file__).parent,
        )

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(len(completed.stderr.splitlines()), 1)
        self.assertTrue(completed.stderr.startswith("error: "))
        self.assertNotIn("Traceback", completed.stderr)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(self.output.read_bytes(), b"keep exactly")

    def test_cli_writes_valid_deterministic_output(self) -> None:
        self.source.write_text(
            "id,title,status\n2,Second,open\n1,First,done\n",
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "task_converter",
                "--input",
                str(self.source),
                "--output",
                str(self.output),
            ],
            check=False,
            capture_output=True,
            text=True,
            cwd=Path(task_converter.__file__).parent,
        )

        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, "")
        self.assertEqual(
            json.loads(self.output.read_text(encoding="utf-8")),
            {
                "rows": [
                    {"id": "2", "title": "Second", "status": "open"},
                    {"id": "1", "title": "First", "status": "done"},
                ],
                "counts": {"done": 1, "open": 1},
            },
        )

    def test_replacement_failure_through_command_entrypoint(self) -> None:
        self.source.write_text("id,title,status\n1,task,open\n", encoding="utf-8")
        self.output.write_bytes(b"keep exactly")

        with mock.patch.object(os, "replace", side_effect=OSError("replace failed")):
            with mock.patch("sys.stderr", new_callable=io.StringIO) as stderr, mock.patch(
                "sys.stdout", new_callable=io.StringIO
            ) as stdout:
                exit_code = task_converter.main(
                    ["--input", str(self.source), "--output", str(self.output)]
                )

        self.assertEqual(exit_code, 2)
        self.assertEqual(len(stderr.getvalue().splitlines()), 1)
        self.assertTrue(stderr.getvalue().startswith("error: "))
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(self.output.read_bytes(), b"keep exactly")
        self.assertEqual(list(Path.cwd().glob(f".{self.output.name}.*.tmp")), [])

    def test_output_preparation_and_write_failures_through_command_entrypoint(self) -> None:
        self.source.write_text("id,title,status\n1,task,open\n", encoding="utf-8")
        failures = ("serialize", "prepare", "open", "write", "flush", "fsync", "replace")

        class FailingWriter:
            def __init__(self, handle, failure: str) -> None:
                self.handle = handle
                self.failure = failure

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                self.handle.close()
                return False

            def write(self, content: str) -> int:
                if self.failure == "write":
                    raise OSError("write failed")
                return self.handle.write(content)

            def flush(self) -> None:
                if self.failure == "flush":
                    raise OSError("flush failed")
                self.handle.flush()

            def fileno(self) -> int:
                return self.handle.fileno()

        for failure in failures:
            with self.subTest(failure=failure):
                self.output.write_bytes(b"preserve this output")
                stderr = io.StringIO()
                stdout = io.StringIO()
                with mock.patch("sys.stderr", stderr), mock.patch("sys.stdout", stdout):
                    if failure == "serialize":
                        patcher = mock.patch.object(
                            task_converter,
                            "_serialize",
                            side_effect=task_converter.ValidationError("serialize failed"),
                        )
                    elif failure == "prepare":
                        patcher = mock.patch.object(
                            task_converter.tempfile,
                            "mkstemp",
                            side_effect=OSError("prepare failed"),
                        )
                    elif failure == "open":
                        patcher = mock.patch.object(
                            task_converter.os,
                            "fdopen",
                            side_effect=OSError("open failed"),
                        )
                    elif failure in {"write", "flush"}:
                        real_fdopen = os.fdopen
                        patcher = mock.patch.object(
                            task_converter.os,
                            "fdopen",
                            side_effect=lambda descriptor, *args, **kwargs: FailingWriter(
                                real_fdopen(descriptor, *args, **kwargs), failure
                            ),
                        )
                    elif failure == "fsync":
                        patcher = mock.patch.object(os, "fsync", side_effect=OSError("fsync failed"))
                    else:
                        patcher = mock.patch.object(os, "replace", side_effect=OSError("replace failed"))

                    with patcher:
                        exit_code = task_converter.main(
                            ["--input", str(self.source), "--output", str(self.output)]
                        )

                self.assertEqual(exit_code, 2)
                self.assertEqual(len(stderr.getvalue().splitlines()), 1)
                self.assertTrue(stderr.getvalue().startswith("error: "))
                self.assertNotIn("Traceback", stderr.getvalue())
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(self.output.read_bytes(), b"preserve this output")
                self.assertEqual(list(Path.cwd().glob(f".{self.output.name}.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
