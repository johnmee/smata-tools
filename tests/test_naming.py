"""Offline tests for document file naming."""

import unittest

from smata.naming import main_filename
from tests.fakes import doc


class FilenameTests(unittest.TestCase):
    def test_main_filename(self):
        self.assertEqual(main_filename(doc(42, category="Levy/Notice")), "2023-11-14-Levy-Notice-42.pdf")
        self.assertEqual(main_filename(doc(42, category="minute book")), "2023-11-14-minute-book-42.pdf")
        self.assertEqual(main_filename(doc(42, category=None)), "2023-11-14-uncategorised-42.pdf")


if __name__ == "__main__":
    unittest.main()
