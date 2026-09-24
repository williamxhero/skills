import unittest

from task_csv_to_json import ValidationError, parse_csv


class TaskCsvParserTests(unittest.TestCase):
    def test_normalizes_rows_without_reordering_or_trimming_values(self):
        result = parse_csv(
            "status,extra,title,id\n"
            "doing,ignored,  Keep spaces  , 2 \n"
            "todo,metadata,First,1\n"
        )

        self.assertEqual(
            result,
            {
                "rows": [
                    {"id": " 2 ", "title": "  Keep spaces  ", "status": "doing"},
                    {"id": "1", "title": "First", "status": "todo"},
                ],
                "counts": {},
            },
        )

    def test_header_only_csv_is_valid(self):
        self.assertEqual(parse_csv("title,id,status,notes\n"), {"rows": [], "counts": {}})

    def test_rejects_missing_columns(self):
        with self.assertRaisesRegex(ValidationError, "missing required column.*status"):
            parse_csv("id,title\n1,Task\n")

    def test_rejects_blank_rows_and_blank_required_values(self):
        invalid_inputs = (
            "id,title,status\n\n",
            "id,title,status\n1,,todo\n",
            "id,title,status\n1,Task,   \n",
        )
        for csv_text in invalid_inputs:
            with self.subTest(csv_text=repr(csv_text)):
                with self.assertRaises(ValidationError):
                    parse_csv(csv_text)

    def test_rejects_malformed_quoting(self):
        with self.assertRaisesRegex(ValidationError, "malformed CSV"):
            parse_csv('id,title,status\n1,"unfinished,todo\n')

    def test_rejects_duplicate_nonblank_ids(self):
        with self.assertRaisesRegex(ValidationError, "duplicate nonblank id"):
            parse_csv("id,title,status\n1,First,todo\n1,Second,done\n")


if __name__ == "__main__":
    unittest.main()
