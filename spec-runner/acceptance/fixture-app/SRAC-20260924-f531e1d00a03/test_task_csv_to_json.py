import io
import subprocess
import sys
import unittest
import uuid
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from task_csv_to_json import main


COMMAND = Path(__file__).with_name("task_csv_to_json.py")


class TaskCsvToJsonTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(__file__).parent
        self.prefix = f".task-csv-to-json-{uuid.uuid4().hex}"

    def tearDown(self):
        for path in self.directory.glob(f"{self.prefix}*"):
            path.unlink(missing_ok=True)

    def run_command(self, input_bytes: bytes, output: Path | None = None):
        input_path = self.directory / f"{self.prefix}-tasks.csv"
        input_path.write_bytes(input_bytes)
        output_path = output or self.directory / "tasks.json"
        return subprocess.run(
            [sys.executable, str(COMMAND), str(input_path), str(output_path)],
            capture_output=True,
        ), output_path

    def test_success_accepts_reordered_and_extra_columns_and_preserves_values(self):
        result, output = self.run_command(
            "status,notes,title,id\n doing ,ignored, Café , 2 \nunknown,metadata,First task,1\n".encode()
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")
        self.assertEqual(
            output.read_bytes(),
            b'{"tasks":[{"id":" 2 ","title":" Caf\xc3\xa9 ","status":" doing "},{"id":"1","title":"First task","status":"unknown"}]}\n',
        )

    def test_header_only_csv_is_valid(self):
        result, output = self.run_command(b"id,title,status\n")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(output.read_bytes(), b'{"tasks":[]}\n')

    def test_validation_failures_do_not_create_or_modify_destination(self):
        invalid_inputs = [
            b"\xffid,title,status\n",
            b"id,title,status\n1,\"unterminated,todo\n",
            b"id,name,status\n1,One,todo\n",
            b"id,title,status\n\n",
            b"id,title,status\n ,One,todo\n",
            b"id,title,status\n1, ,todo\n",
            b"id,title,status\n1,One,\n",
            b"id,title,status\n1,One,todo\n1,Other,done\n",
            b"id,title,status\n1,One,\n",
        ]
        for index, csv_data in enumerate(invalid_inputs):
            with self.subTest(index=index):
                output = self.directory / f"{self.prefix}-invalid-{index}.json"
                result, _ = self.run_command(csv_data, output)
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(result.stderr)
                self.assertFalse(output.exists())

        existing = self.directory / f"{self.prefix}-existing.json"
        original = b'{"tasks":[{"id":"old","title":"Old","status":"done"}]}\n'
        existing.write_bytes(original)
        result, _ = self.run_command(b"id,title,status\n1,,todo\n", existing)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(existing.read_bytes(), original)

    def test_cli_requires_exactly_two_paths(self):
        missing = subprocess.run([sys.executable, str(COMMAND)], capture_output=True)
        extra = subprocess.run(
            [sys.executable, str(COMMAND), "in.csv", "out.json", "extra"],
            capture_output=True,
        )
        self.assertNotEqual(missing.returncode, 0)
        self.assertNotEqual(extra.returncode, 0)
        self.assertTrue(missing.stderr)
        self.assertTrue(extra.stderr)

    def test_repeated_runs_are_byte_identical_and_replace_existing_output(self):
        data = b"id,title,status\na,Alpha,todo\nb,Beta,done\n"
        first, output = self.run_command(data)
        first_bytes = output.read_bytes()
        second, _ = self.run_command(data, output)
        self.assertEqual(first.returncode, 0)
        self.assertEqual(second.returncode, 0)
        self.assertEqual(output.read_bytes(), first_bytes)

    def test_replacement_failure_preserves_destination_and_cleans_temp(self):
        input_path = self.directory / "tasks.csv"
        input_path.write_bytes(b"id,title,status\n1,One,todo\n")
        output = self.directory / f"{self.prefix}-existing.json"
        original = b"old\n"
        output.write_bytes(original)
        with mock.patch("task_csv_to_json.os.replace", side_effect=OSError("simulated failure")):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                returncode = main([str(input_path), str(output)])
        self.assertNotEqual(returncode, 0)
        self.assertIn("error:", stderr.getvalue())
        self.assertEqual(output.read_bytes(), original)
        self.assertEqual(list(self.directory.glob(f".{output.name}.*.tmp")), [])

    def test_malformed_quoting_in_ignored_column_is_rejected(self):
        output_path = self.directory / f"{self.prefix}-malformed.json"
        result, output = self.run_command(b"id,title,status,notes\n1,One,todo,bad\"quote\n", output_path)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
