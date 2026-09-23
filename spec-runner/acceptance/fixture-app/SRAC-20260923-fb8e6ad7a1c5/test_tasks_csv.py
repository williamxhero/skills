import contextlib
import io
import json
import shutil
import unittest
from pathlib import Path

from tasks_csv import main


class TaskCsvTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(__file__).parent / ".test-data"
        self.directory.mkdir(exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_valid_csv_produces_stable_json(self):
        source = self.directory / "tasks.csv"
        source.write_text('id,title,status\n2,"Fix, then test",done\n1, Write docs , open \n', encoding="utf-8")
        output = self.directory / "tasks.json"
        self.assertEqual(main([str(source), str(output)]), 0)
        first = output.read_bytes()
        self.assertEqual(main([str(source), str(output)]), 0)
        self.assertEqual(output.read_bytes(), first)
        self.assertEqual(json.loads(first), [
            {"id": "2", "title": "Fix, then test", "status": "done"},
            {"id": "1", "title": "Write docs", "status": "open"},
        ])

    def test_invalid_rows_leave_existing_output_intact(self):
        invalid = [
            "id,title,status\n1,Only two\n",
            "id,title,status\n1,Good,open,extra\n",
            "id,title,status\n1,,open\n",
            "id,title,status\n1,A,open\n1,B,done\n",
            "title,id,status\nA,1,open\n",
            'id,title,status\n1,"unterminated,open\n',
        ]
        source = self.directory / "tasks.csv"
        output = self.directory / "tasks.json"
        for content in invalid:
            with self.subTest(content=content):
                source.write_text(content, encoding="utf-8")
                output.write_text("original", encoding="utf-8")
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(main([str(source), str(output)]), 1)
                self.assertEqual(output.read_text(encoding="utf-8"), "original")

    def test_stdout_is_empty_on_invalid_input(self):
        source = self.directory / "tasks.csv"
        source.write_text("id,title,status\n1,Good,open\n2,,done\n", encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main([str(source)]), 1)
        self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
