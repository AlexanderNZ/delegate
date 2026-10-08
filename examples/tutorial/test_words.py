import unittest

from words import count_words


class WordsTest(unittest.TestCase):
    def test_count_words(self):
        self.assertEqual(count_words("one two  three"), 3)
