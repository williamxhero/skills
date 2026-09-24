import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("task_csv_to_json.py")


class TaskCsvToJsonCliTests(unittest.TestCase):
    def temporary_directory(self) -> tempfile.TemporaryDirectory[str]:
        # The acceptance runner grants write access to the fixture workspace,
        # while the host temp directory may be outside the sandbox.
        return tempfile.TemporaryDirectory(dir=SCRIPT.parent)

    def run_cli(self, *args: Path | str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *(str(arg) for arg in args)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

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
            source.write_text("id,title,status\n", encoding="utf-8")

            result = self.run_cli(source, destination)

            self.assertEqual(result.returncode, 0)
            self.assertEqual(destination.read_bytes(), b"[]\n")

    def test_validation_failure_preserves_existing_destination(self) -> None:
        with self.temporary_directory() as directory:
            root = Path(directory)
            source = root / "tasks.csv"
            destination = root / "tasks.json"
            source.write_text("id,title,status\n1,Task,COMPLETE\n", encoding="utf-8")
            original = b"known-good\n"
            destination.write_bytes(original)

            result = self.run_cli(source, destination)

            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b"")
            self.assertIn(b"validation", result.stderr)
            self.assertIn(b"row 2", result.stderr)
            self.assertEqual(destination.read_bytes(), original)

    def test_invalid_utf8_is_input_format_error(self) -> None:
        with self.temporary_directory() as directory:
            root = Path(directory)
            source = root / "tasks.csv"
            destination = root / "tasks.json"
            source.write_bytes(b"id,title,status\n1,Task,\xff\n")

            result = self.run_cli(source, destination)

            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b"")
            self.assertIn(b"input-format", result.stderr)
            self.assertFalse(destination.exists())

    def test_same_file_is_rejected_without_modification(self) -> None:
        with self.temporary_directory() as directory:
            source = Path(directory) / "tasks.csv"
            original = b"id,title,status\n1,Task,pending\n"
            source.write_bytes(original)

            result = self.run_cli(source, source)

            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b"")
            self.assertIn(b"different files", result.stderr)
            self.assertEqual(source.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
