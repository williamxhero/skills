import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_DIR = Path(__file__).resolve().parent


class TaskCsvToJsonProcessTests(unittest.TestCase):
    def run_command(self, input_path, output_path):
        return subprocess.run(
            [sys.executable, "-m", "task_csv_to_json", str(input_path), str(output_path)],
            cwd=MODULE_DIR,
            capture_output=True,
        )

    def write_input(self, directory, content, encoding="utf-8"):
        input_path = directory / "tasks.csv"
        input_path.write_text(content, encoding=encoding, newline="")
        return input_path

    def test_valid_csv_is_compact_deterministic_utf8_json(self):
        with tempfile.TemporaryDirectory(dir=MODULE_DIR) as temporary_directory:
            directory = Path(temporary_directory)
            input_path = self.write_input(
                directory,
                'id,title,status\r\n'
                ' first ," Task, one ", todo\r\n'
                '二,"say ""hello""\nworld",done\r\n',
            )
            output_path = directory / "tasks.json"

            first = self.run_command(input_path, output_path)
            first_bytes = output_path.read_bytes()
            second = self.run_command(input_path, output_path)

            self.assertEqual(first.returncode, 0, first.stderr.decode())
            self.assertEqual(second.returncode, 0, second.stderr.decode())
            self.assertEqual(first.stdout, b"")
            self.assertEqual(second.stdout, b"")
            self.assertEqual(first_bytes, output_path.read_bytes())
            self.assertEqual(
                first_bytes,
                (
                    '{"tasks":[{"id":"first","title":"Task, one","status":"todo"},'
                    '{"id":"二","title":"say \\"hello\\"\\nworld","status":"done"}]}\n'
                ).encode(),
            )

    def test_bom_and_header_only_input(self):
        with tempfile.TemporaryDirectory(dir=MODULE_DIR) as temporary_directory:
            directory = Path(temporary_directory)
            input_path = self.write_input(directory, "id,title,status\r\n", encoding="utf-8-sig")
            output_path = directory / "tasks.json"

            result = self.run_command(input_path, output_path)

            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(output_path.read_bytes(), b'{"tasks":[]}\n')

    def test_validation_failure_has_no_stdout_and_preserves_output(self):
        with tempfile.TemporaryDirectory(dir=MODULE_DIR) as temporary_directory:
            directory = Path(temporary_directory)
            input_path = self.write_input(
                directory,
                "id,title,status\none,Task,todo\none,Other,done\n",
            )
            output_path = directory / "tasks.json"
            original = b'{"tasks":[{"id":"known"}]}\n'
            output_path.write_bytes(original)

            result = self.run_command(input_path, output_path)

            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, b"")
            self.assertNotEqual(result.stderr, b"")
            self.assertEqual(output_path.read_bytes(), original)
            self.assertEqual(list(directory.glob("*.tmp")), [])
            self.assertEqual(list(directory.glob(".tasks.json.*")), [])

    def test_strict_header_and_field_validation(self):
        invalid_inputs = [
            " id,title,status\none,Task,todo\n",
            "title,id,status\nTask,one,todo\n",
            "id,title,status\none,Task\n",
            "id,title,status\none,Task,blocked\n",
            "id,title,status\n,Task,todo\n",
            'id,title,status\none,"unterminated,todo\n',
        ]
        with tempfile.TemporaryDirectory(dir=MODULE_DIR) as temporary_directory:
            directory = Path(temporary_directory)
            output_path = directory / "tasks.json"
            for index, content in enumerate(invalid_inputs):
                input_path = directory / f"tasks-{index}.csv"
                input_path.write_text(content, encoding="utf-8", newline="")
                result = self.run_command(input_path, output_path)
                self.assertEqual(result.returncode, 1, content)
                self.assertEqual(result.stdout, b"")
                self.assertNotEqual(result.stderr, b"")

    def test_input_output_aliases_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=MODULE_DIR) as temporary_directory:
            directory = Path(temporary_directory)
            input_path = self.write_input(directory, "id,title,status\none,Task,todo\n")
            output_path = directory / "tasks.json"
            try:
                os.link(input_path, output_path)
            except OSError:
                self.skipTest("hard links are unavailable")

            result = self.run_command(input_path, input_path)
            alias_result = self.run_command(input_path, output_path)

            self.assertEqual(result.returncode, 1)
            self.assertEqual(alias_result.returncode, 1)
            self.assertEqual(input_path.read_text(encoding="utf-8"), "id,title,status\none,Task,todo\n")

    def test_missing_output_directory_does_not_get_created(self):
        with tempfile.TemporaryDirectory(dir=MODULE_DIR) as temporary_directory:
            directory = Path(temporary_directory)
            input_path = self.write_input(directory, "id,title,status\none,Task,todo\n")
            missing_directory = directory / "missing"
            output_path = missing_directory / "tasks.json"

            result = self.run_command(input_path, output_path)

            self.assertEqual(result.returncode, 1)
            self.assertFalse(missing_directory.exists())

    @unittest.skipUnless(hasattr(os, "symlink"), "symbolic links are unavailable")
    def test_output_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=MODULE_DIR) as temporary_directory:
            directory = Path(temporary_directory)
            input_path = self.write_input(directory, "id,title,status\none,Task,todo\n")
            target_path = directory / "target.json"
            target_path.write_bytes(b"known\n")
            output_path = directory / "tasks.json"
            try:
                output_path.symlink_to(target_path)
            except (OSError, NotImplementedError):
                self.skipTest("symbolic links are unavailable")

            result = self.run_command(input_path, output_path)

            self.assertEqual(result.returncode, 1)
            self.assertEqual(target_path.read_bytes(), b"known\n")

    def test_invalid_utf8_and_unreadable_input_fail_cleanly(self):
        with tempfile.TemporaryDirectory(dir=MODULE_DIR) as temporary_directory:
            directory = Path(temporary_directory)
            invalid_path = directory / "invalid.csv"
            invalid_path.write_bytes(b"id,title,status\none,\xff,todo\n")
            output_path = directory / "tasks.json"

            invalid_result = self.run_command(invalid_path, output_path)

            self.assertEqual(invalid_result.returncode, 1)
            self.assertEqual(invalid_result.stdout, b"")
            self.assertFalse(output_path.exists())

            unreadable_path = directory / "missing.csv"
            unreadable_result = self.run_command(unreadable_path, output_path)

            self.assertEqual(unreadable_result.returncode, 1)
            self.assertEqual(unreadable_result.stdout, b"")


if __name__ == "__main__":
    unittest.main()
