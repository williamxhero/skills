import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("task_csv_to_json.py")


class TaskCsvToJsonCliTests(unittest.TestCase):
    def temporary_directory(self) -> tempfile.TemporaryDirectory[str]:
        # Keep test files inside the assigned workspace for restricted runners.
        return tempfile.TemporaryDirectory(dir=SCRIPT.parent)

    def run_cli(self, *args: Path | str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *(str(arg) for arg in args)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def assert_failure(
        self,
        result: subprocess.CompletedProcess[bytes],
        code: int,
        category: bytes,
    ) -> None:
        self.assertEqual(result.returncode, code)
        self.assertEqual(result.stdout, b"")
        self.assertTrue(result.stderr.endswith(b"\n"))
        self.assertEqual(result.stderr.count(b"\n"), 1)
        self.assertIn(category, result.stderr)

    def test_success_is_deterministic_and_supports_csv_features(self) -> None:
        with self.temporary_directory() as directory:
            root = Path(directory)
            source = root / "tasks.csv"
            destination = root / "tasks.json"
            source.write_bytes(
                (
                    "\ufeffid,title,status\r\n"
                    "  1 ,\"Hello, 世界\",pending\r\n"
                    "2,\"multi\nline\", done\r\n"
                ).encode("utf-8")
            )

            result = self.run_cli(source, destination)

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b"")
            self.assertEqual(result.stderr, b"")
            self.assertEqual(
                destination.read_bytes(),
                '[{"id":"1","title":"Hello, 世界","status":"pending"},'
                '{"id":"2","title":"multi\\nline","status":"done"}]\n'.encode("utf-8"),
            )

    def test_header_only_input_writes_empty_array(self) -> None:
        with self.temporary_directory() as directory:
            root = Path(directory)
            source = root / "tasks.csv"
            destination = root / "tasks.json"
            source.write_bytes(b"id,title,status\n")

            result = self.run_cli(source, destination)

            self.assertEqual(result.returncode, 0)
            self.assertEqual(destination.read_bytes(), b"[]\n")

    def test_validation_and_structure_failures_preserve_destination(self) -> None:
        cases = [
            (b"id,status,title\n1,pending,Task\n", b"validation", b"header"),
            (b"id,title\n1,Task\n", b"input-format", b"row 2"),
            (b"id,title,status\n1,Task,pending,extra\n", b"input-format", b"row 2"),
            (b"id,title,status\n1, ,pending\n", b"validation", b"column title"),
            (b"id,title,status\n ,Task,pending\n", b"validation", b"column id"),
            (b"id,title,status\n1,Task, \n", b"validation", b"column status"),
            (b"id,title,status\n\n", b"validation", b"blank row"),
            (b"id,title,status\n1,Task,pending\n1,Other,done\n", b"validation", b"duplicate id"),
            (b"id,title,status\n1,Task,Pending\n", b"validation", b"invalid status"),
            (b'id,title,status\n1,"unfinished,pending\n', b"input-format", b"malformed CSV"),
        ]
        for csv_bytes, category, diagnostic in cases:
            with self.subTest(csv_bytes=csv_bytes), self.temporary_directory() as directory:
                root = Path(directory)
                source = root / "tasks.csv"
                destination = root / "tasks.json"
                source.write_bytes(csv_bytes)
                destination.write_bytes(b"known-good\n")

                result = self.run_cli(source, destination)

                self.assert_failure(result, 2, category)
                self.assertIn(diagnostic, result.stderr)
                self.assertEqual(destination.read_bytes(), b"known-good\n")

    def test_whitespace_is_trimmed_before_duplicate_and_status_validation(self) -> None:
        with self.temporary_directory() as directory:
            root = Path(directory)
            source = root / "tasks.csv"
            destination = root / "tasks.json"
            source.write_bytes(b"id,title,status\n 7 , Task , done \n")

            result = self.run_cli(source, destination)

            self.assertEqual(result.returncode, 0)
            self.assertEqual(destination.read_bytes(), b'[{"id":"7","title":"Task","status":"done"}]\n')

    def test_validation_row_number_counts_csv_records_not_physical_lines(self) -> None:
        with self.temporary_directory() as directory:
            root = Path(directory)
            source = root / "tasks.csv"
            destination = root / "tasks.json"
            source.write_text('id,title,status\n1,"two\nlines",pending\n2, ,done\n', encoding="utf-8")

            result = self.run_cli(source, destination)

            self.assert_failure(result, 2, b"validation")
            self.assertIn(b"row 3", result.stderr)
            self.assertIn(b"column title", result.stderr)

    def test_invalid_utf8_is_input_format_error(self) -> None:
        with self.temporary_directory() as directory:
            root = Path(directory)
            source = root / "tasks.csv"
            destination = root / "tasks.json"
            source.write_bytes(b"id,title,status\n1,Task,\xff\n")
            destination.write_bytes(b"known-good\n")

            result = self.run_cli(source, destination)

            self.assert_failure(result, 2, b"input-format")
            self.assertEqual(destination.read_bytes(), b"known-good\n")

    def test_usage_error_has_one_diagnostic_and_no_stdout(self) -> None:
        result = self.run_cli()

        self.assert_failure(result, 2, b"usage")

    def test_missing_input_is_operational_and_preserves_destination(self) -> None:
        with self.temporary_directory() as directory:
            root = Path(directory)
            destination = root / "tasks.json"
            destination.write_bytes(b"known-good\n")

            result = self.run_cli(root / "missing.csv", destination)

            self.assert_failure(result, 1, b"operational")
            self.assertEqual(destination.read_bytes(), b"known-good\n")

    def test_directory_input_is_reported_as_operational(self) -> None:
        with self.temporary_directory() as directory:
            root = Path(directory)
            destination = root / "tasks.json"
            destination.write_bytes(b"known-good\n")

            result = self.run_cli(root, destination)

            self.assert_failure(result, 1, b"operational")
            self.assertEqual(destination.read_bytes(), b"known-good\n")

    def test_missing_output_parent_is_not_created(self) -> None:
        with self.temporary_directory() as directory:
            root = Path(directory)
            source = root / "tasks.csv"
            missing_parent = root / "missing"
            source.write_bytes(b"id,title,status\n1,Task,pending\n")

            result = self.run_cli(source, missing_parent / "tasks.json")

            self.assert_failure(result, 1, b"operational")
            self.assertFalse(missing_parent.exists())

    def test_same_file_is_rejected_without_modification(self) -> None:
        with self.temporary_directory() as directory:
            source = Path(directory) / "tasks.csv"
            original = b"id,title,status\n1,Task,pending\n"
            source.write_bytes(original)

            result = self.run_cli(source, source)

            self.assert_failure(result, 2, b"validation")
            self.assertEqual(source.read_bytes(), original)

    def test_successful_conversion_atomically_overwrites_existing_destination(self) -> None:
        with self.temporary_directory() as directory:
            root = Path(directory)
            source = root / "tasks.csv"
            destination = root / "tasks.json"
            source.write_bytes(b"id,title,status\n1,Task,pending\n")
            destination.write_bytes(b"old output\n")

            result = self.run_cli(source, destination)

            self.assertEqual(result.returncode, 0)
            self.assertEqual(destination.read_bytes(), b'[{"id":"1","title":"Task","status":"pending"}]\n')
            self.assertEqual(list(root.glob(f".{destination.name}.*.tmp")), [])

    def test_output_replace_failure_preserves_destination_and_cleans_temporary(self) -> None:
        with self.temporary_directory() as directory:
            root = Path(directory)
            source = root / "tasks.csv"
            destination_directory = root / "destination.json"
            source.write_bytes(b"id,title,status\n1,Task,pending\n")
            destination_directory.mkdir()
            marker = destination_directory / "preserve.txt"
            marker.write_bytes(b"unchanged")

            result = self.run_cli(source, destination_directory)

            self.assert_failure(result, 1, b"operational")
            self.assertEqual(marker.read_bytes(), b"unchanged")
            self.assertEqual(list(root.glob(f".{destination_directory.name}.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
