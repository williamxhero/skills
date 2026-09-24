from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


COMMAND = Path(__file__).with_name("task_csv.py")


class TaskCsvCommandTests(unittest.TestCase):
    def run_command(self, input_path: Path, output_path: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(COMMAND), "--input", str(input_path), "--output", str(output_path)],
            capture_output=True,
            text=True,
            check=False,
        )

    def write_input(self, directory: Path, content: str) -> Path:
        input_path = directory / "tasks.csv"
        input_path.write_text(content, encoding="utf-8", newline="")
        return input_path

    def test_success_preserves_order_and_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            input_path = self.write_input(
                directory,
                "id,title,status\n2,Second,doing\n1,First,todo\n3,Third,done\n",
            )
            output_path = directory / "tasks.json"

            result = self.run_command(input_path, output_path)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(output_path.read_text(encoding="utf-8")),
                {
                    "tasks": [
                        {"id": "2", "title": "Second", "status": "doing"},
                        {"id": "1", "title": "First", "status": "todo"},
                        {"id": "3", "title": "Third", "status": "done"},
                    ]
                },
            )

    def test_identical_inputs_produce_identical_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            input_path = self.write_input(directory, "id,title,status\n1,Café,todo\n")
            first_output = directory / "first.json"
            second_output = directory / "second.json"

            first_result = self.run_command(input_path, first_output)
            second_result = self.run_command(input_path, second_output)

            self.assertEqual(first_result.returncode, 0, first_result.stderr)
            self.assertEqual(second_result.returncode, 0, second_result.stderr)
            self.assertEqual(first_output.read_bytes(), second_output.read_bytes())

    def test_non_exact_headers_are_rejected(self) -> None:
        invalid_headers = [
            "id,title\n1,Only two\n",
            "id,title,status,extra\n1,Task,todo,unexpected\n",
            "title,id,status\nTask,1,todo\n",
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            for index, content in enumerate(invalid_headers):
                with self.subTest(content=content):
                    input_path = self.write_input(directory, content)
                    output_path = directory / f"invalid-{index}.json"
                    result = self.run_command(input_path, output_path)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(output_path.exists())

    def test_empty_required_fields_and_invalid_status_are_rejected(self) -> None:
        invalid_rows = [
            "id,title,status\n,Task,todo\n",
            "id,title,status\n1,   ,todo\n",
            "id,title,status\n1,Task,blocked\n",
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            for index, content in enumerate(invalid_rows):
                with self.subTest(content=content):
                    input_path = self.write_input(directory, content)
                    output_path = directory / f"invalid-row-{index}.json"
                    result = self.run_command(input_path, output_path)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(output_path.exists())

    def test_failure_preserves_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            input_path = self.write_input(directory, "id,title,status\n1,Task,nope\n")
            output_path = directory / "tasks.json"
            original = b'{"tasks":[{"id":"old"}]}\n'
            output_path.write_bytes(original)

            result = self.run_command(input_path, output_path)

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(output_path.read_bytes(), original)

    def test_malformed_row_is_rejected_without_partial_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            input_path = self.write_input(directory, "id,title,status\n1,Task,todo,extra\n")
            output_path = directory / "tasks.json"

            result = self.run_command(input_path, output_path)

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output_path.exists())


if __name__ == "__main__":
    unittest.main()
