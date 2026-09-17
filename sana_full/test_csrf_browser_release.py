import unittest

from test_company_memory import CompanyMemoryTest


class CsrfBrowserReleaseTest(unittest.TestCase):
    """Run the CSRF contract without CompanyMemoryTest's database fixtures."""

    test_templates_use_only_the_shared_csrf_fetch_guard = (
        CompanyMemoryTest.test_templates_use_only_the_shared_csrf_fetch_guard
    )
    test_shared_csrf_fetch_guard_runs_in_browser = (
        CompanyMemoryTest.test_shared_csrf_fetch_guard_runs_in_browser
    )


if __name__ == "__main__":
    unittest.main()