import unittest

from core.outreach.nvoids_links import is_nvoids_job_detail_url


class NvoidsJobDetailLinkTests(unittest.TestCase):
    def test_current_route_is_recognized(self):
        self.assertTrue(
            is_nvoids_job_detail_url("https://jobs.nvoids.com/j_details.jsp?id=123")
        )

    def test_legacy_route_is_still_recognized(self):
        self.assertTrue(
            is_nvoids_job_detail_url("https://jobs.nvoids.com/job_details.jsp?id=123")
        )

    def test_unrelated_links_are_not_recognized(self):
        self.assertFalse(is_nvoids_job_detail_url("https://jobs.nvoids.com/index.jsp"))
        self.assertFalse(is_nvoids_job_detail_url("https://jobs.nvoids.com/j_details.jspx?id=123"))


if __name__ == "__main__":
    unittest.main()
