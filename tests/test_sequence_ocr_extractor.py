import unittest

from xml.etree.ElementTree import Element

from pdf_craft.analysers.sequence.ocr_extractor import _Sequence


class TestSequenceOcrExtractor(unittest.TestCase):

  def test_iter_line_ids_supports_normal_values(self):
    sequence = _Sequence.__new__(_Sequence)

    self.assertEqual(
      list(sequence._iter_line_ids(Element("line", {"id": "12-15"}))),
      [12, 13, 14, 15],
    )
    self.assertEqual(
      list(sequence._iter_line_ids(Element("line", {"id": "7"}))),
      [7],
    )

  def test_iter_line_ids_tolerates_malformed_values(self):
    sequence = _Sequence.__new__(_Sequence)

    self.assertEqual(
      list(sequence._iter_line_ids(Element("line", {"id": ""}))),
      [],
    )
    self.assertEqual(
      list(sequence._iter_line_ids(Element("line", {"id": "12-"}))),
      [12],
    )
    self.assertEqual(
      list(sequence._iter_line_ids(Element("line", {"id": "-15"}))),
      [15],
    )
    self.assertEqual(
      list(sequence._iter_line_ids(Element("line", {"id": "15 - 12"}))),
      [12, 13, 14, 15],
    )
    self.assertEqual(
      list(sequence._iter_line_ids(Element("line", {"id": "abc"}))),
      [],
    )
