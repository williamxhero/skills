import io
import json
import subprocess
import sys
import tempfile
import uuid
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from task_csv_to_json import main


COMMAND = Path(__file__).with_name("task_csv_to_json.py")


class TaskCsvToJsonTests(unittest.TestCase):
    def run_command(self, input_bytes: bytes, output: Path | None = None):
        input_path = self.directory / f"{self.prefix}-tasks.csv"
        input_path.write_bytes(input_bytes)
        output_path = output or self.directory / f"{self.prefix}-tasks.json"
        result = subprocess.run(
            [sys.executable, str(COMMAND), str(input_path), str(output_path)],
            capture_output=True,
        )
        return result, output_path

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="task-csv-to-json-")
        self.directory = Path(self.tempdir.name)
        self.prefix = f".test-{uuid.uuid4().hex}"

    def tearDown(self):
        self.tempdir.cleanup()

    def test_success_has_canonical_bytes_and_preserves_order(self):
        result, output = self.run_command(
            "id,title,status\n 2 , Café,doing\n1,First task,todo\n".encode()
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")
        self.assertEqual(
            output.read_bytes(),
            b'{"tasks":[{"id":"2","title":"Caf\xc3\xa9","status":"doing"},{"id":"1","title":"First task","status":"todo"}]}\n',
        )
        self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["tasks"][0]["id"], "2")

    def test_header_only_csv_is_valid(self):
        result, output = self.run_command(b"id,title,status\n")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(output.read_bytes(), b'{"tasks":[]}\n')

    def test_invalid_inputs_do_not_create_or_modify_destination(self):
        invalid_inputs = [
            b"\xffid,title,status\n",
            b"id,title,status\n1,\"unterminated,todo\n",
            b"id,name,status\n1,One,todo\n",
            b"id,title,status\n1,One,todo,extra\n",
            b"id,title,status\n\n",
            b"id,title,status\n ,One,todo\n",
            b"id,title,status\n1, ,todo\n",
            b"id,title,status\n1,One,todo\n1,Other,done\n",
            b"id,title,status\n1,One,TODO\n",
        ]
        for index, csv_data in enumerate(invalid_inputs):
            with self.subTest(index=index):
                output = self.directory / f"{self.prefix}-invalid-{index}.json"
                result, output = self.run_command(csv_data, output)
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

    def test_replacement_failure_preserves_destination(self):
        input_path = self.directory / f"{self.prefix}-tasks.csv"
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
        self.assertEqual(list(self.directory.glob(".*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
