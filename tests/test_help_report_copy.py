"""Guard the REPORT.md explanation readers reach in How it works."""

import html
import re
import unittest
from pathlib import Path


class TestHelpReportOdometerCopy(unittest.TestCase):
    def setUp(self):
        source = (Path(__file__).resolve().parents[1] / "docs" / "how.html").read_text(
            encoding="utf-8")
        paragraphs = [" ".join(html.unescape(re.sub(r"<[^>]+>", "", p)).split())
                      for p in re.findall(r"<p\b[^>]*>(.*?)</p>", source, re.S)]
        matches = [p for p in paragraphs
                   if p.startswith("The daily REPORT.md is the same claims in a file")]
        self.assertEqual(len(matches), 1, "check the actual report-explanation paragraph")
        self.report = matches[0]

    def test_the_median_split_names_recorded_mileage_groups_and_support(self):
        for inference in ("where dealer stock sits beside used cars",
                          "49 stock cars", "61 used at 21"):
            with self.subTest(inference=inference):
                self.assertNotIn(inference, self.report)
        for supported in ("under 100 miles versus 100+ miles",
                          "twelve dated on each side", "each group gets its own median",
                          "49 cars under 100 miles at 78 days",
                          "61 at 100+ miles at 21", "unknown mileage is unclassified",
                          "typical-days figure waits for twelve dated cars"):
            with self.subTest(supported=supported):
                self.assertIn(supported, self.report)

    def test_the_arrival_label_keeps_its_cohort_without_a_use_determination(self):
        self.assertNotIn("not a used i7 anyone is choosing between", self.report)
        for supported in ("cohort and its size", "from 45 such cars",
                          "under 100 recorded miles",
                          "not a determination of ownership, dealer use, legal new/used status, "
                          "or certification validity",
                          "unknown mileage is unclassified"):
            with self.subTest(supported=supported):
                self.assertIn(supported, self.report)
