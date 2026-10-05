import unittest
from unittest.mock import patch

import app


class PrintDashboardTests(unittest.TestCase):
    def test_page_ranges_are_validated_and_deduplicated(self):
        self.assertEqual(app.parse_page_spec("1-3,3-5", 5), [1, 2, 3, 4, 5])
        with self.assertRaises(ValueError): app.parse_page_spec("0,2", 5)
        with self.assertRaises(ValueError): app.parse_page_spec("4-2", 5)
        with self.assertRaises(ValueError): app.parse_page_spec("1-9", 5)

    def test_copies_are_validated(self):
        with self.assertRaises(ValueError): app.validate_request({"copies": 0}, 5)
        with self.assertRaises(ValueError): app.validate_request({"copies": 101}, 5)

    @patch("app.subprocess.run")
    @patch("app.sumatra_path")
    @patch("app.default_printer_name", return_value="Test Printer")
    def test_print_command_is_mocked_and_safe(self, printer, sumatra, run):
        sumatra.return_value.is_file.return_value = True
        path = app.PDF_DIR / "demo.pdf"
        path.touch()
        try:
            app.print_pdf("demo", "1-3", 2, False)
            args = run.call_args.args[0]
            self.assertIn("-print-to-default", args)
            self.assertIn("2x,monochrome,1-3", args)
            run.assert_called_once()
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
