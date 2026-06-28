"""Tests for iGIN0 search error hierarchy."""

import unittest


class TestSearchErrors(unittest.TestCase):
    def test_base_error(self):
        from deerflow.community.searxng.search_errors import SearchError

        err = SearchError("test msg")
        self.assertEqual(str(err), "test msg")
        self.assertIsInstance(err, Exception)

    def test_transient_error(self):
        from deerflow.community.searxng.search_errors import SearchTransientError

        err = SearchTransientError("timeout")
        self.assertIsInstance(err, Exception)

    def test_connection_error(self):
        from deerflow.community.searxng.search_errors import SearchConnectionError

        err = SearchConnectionError("unreachable")
        self.assertIsInstance(err, Exception)

    def test_timeout_error(self):
        from deerflow.community.searxng.search_errors import SearchTimeoutError

        err = SearchTimeoutError("deadline exceeded")
        self.assertIsInstance(err, Exception)

    def test_permanent_error(self):
        from deerflow.community.searxng.search_errors import SearchPermanentError

        err = SearchPermanentError("bad query")
        self.assertIsInstance(err, Exception)

    def test_unavailable_error(self):
        from deerflow.community.searxng.search_errors import SearchUnavailableError

        err = SearchUnavailableError("no backends")
        self.assertIsInstance(err, Exception)

    def test_circuit_open_error(self):
        from deerflow.community.searxng.search_errors import SearchCircuitOpenError

        err = SearchCircuitOpenError("open", cooldown_remaining_s=30.0)
        self.assertEqual(err.cooldown_remaining_s, 30.0)

    def test_hierarchy(self):
        from deerflow.community.searxng.search_errors import (
            SearchCircuitOpenError,
            SearchError,
            SearchPermanentError,
            SearchTransientError,
            SearchUnavailableError,
        )

        self.assertTrue(issubclass(SearchTransientError, SearchError))
        self.assertTrue(issubclass(SearchPermanentError, SearchError))
        self.assertTrue(issubclass(SearchUnavailableError, SearchError))
        self.assertTrue(issubclass(SearchCircuitOpenError, SearchError))

    def test_context(self):
        from deerflow.community.searxng.search_errors import SearchError

        err = SearchError("err", context={"key": "val"})
        self.assertEqual(err.context["key"], "val")

    def test_with_context(self):
        from deerflow.community.searxng.search_errors import SearchError

        err = SearchError("err").with_context(http_status=500)
        self.assertEqual(err.context["http_status"], 500)

    def test_is_transient(self):
        from deerflow.community.searxng.search_errors import (
            SearchConnectionError,
            SearchPermanentError,
            SearchTimeoutError,
            SearchTransientError,
            is_permanent,
            is_transient,
        )

        self.assertTrue(is_transient(SearchTransientError()))
        self.assertTrue(is_transient(SearchConnectionError()))
        self.assertTrue(is_transient(SearchTimeoutError()))
        self.assertFalse(is_transient(SearchPermanentError()))
        self.assertTrue(is_permanent(SearchPermanentError()))


if __name__ == "__main__":
    unittest.main()
