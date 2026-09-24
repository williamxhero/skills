import builtins
import io
import unittest
from unittest.mock import patch

import task_csv_to_json as converter


class CsvParsingTests(unittest.TestCase):
    def read_bytes(self, data: bytes):
        with patch.object(builtins, "open", return_value=io.BytesIO(data)):
            return converter._read_records("input.csv")

    def test_header_only_input(self):
        self.assertEqual(self.read_bytes(b"id,title,status\r\n"), [])
        self.assertEqual(converter._serialize([]), b"[]\n")

    def test_bom_cr_rows_trimming_order_and_embedded_newlines(self):
        data = (
            b'\xef\xbb\xbf' + b'id,title,status\r'
            b'1," first\rsecond ",open\r'
            b'2,second,custom status'
        )
        self.assertEqual(
            self.read_bytes(data),
            [
                {"id": "1", "title": "first\rsecond", "status": "open"},
                {"id": "2", "title": "second", "status": "custom status"},
            ],
        )

    def test_blank_record_is_rejected(self):
        with self.assertRaises(converter.ValidationError) as caught:
            self.read_bytes(b"id,title,status\n1,a,open\n\n")
        self.assertEqual(caught.exception.reason, "blank record")
        self.assertEqual(caught.exception.row, 3)

    def test_invalid_quotes_and_duplicate_ids_are_rejected(self):
        for data, reason in (
            (b'id,title,status\n1,"broken,open\n', "malformed CSV quoting"),
            (b'id,title,status\n1,ab"cd,open\n', "malformed CSV quoting"),
            (b"id,title,status\n1,first,open\n1,second,done\n", "duplicate id"),
        ):
            with self.subTest(reason=reason, data=data):
                with self.assertRaises(converter.ValidationError) as caught:
                    self.read_bytes(data)
                self.assertIn(reason, caught.exception.reason)


if __name__ == "__main__":
    unittest.main()
