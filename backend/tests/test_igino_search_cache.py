"""Tests for iGIN0 search cache (LRU+TTL)."""

import time
import threading
import unittest


class TestSearchCache(unittest.TestCase):

    def _make_cache(self, max_size: int = 3, ttl_s: float = 60.0):
        from deerflow.community.searxng.search_cache import SearchCache, CacheKey
        self._CacheKey = CacheKey
        return SearchCache(ttl_s=ttl_s, max_size=max_size)

    def _key(self, query="q", cats="general", lang="en", page=1):
        return self._CacheKey(query=cats, categories=cats, language=lang, pageno=page)

    def test_put_and_get(self):
        cache = self._make_cache()
        k = self._CacheKey(query="python", categories="general", language="en", pageno=1)
        cache.put(k, [{"title": "Python", "url": "http://python.org"}])
        result = cache.get(k)
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "Python")

    def test_cache_miss(self):
        cache = self._make_cache()
        k = self._CacheKey(query="missing", categories="general", language="en", pageno=1)
        result = cache.get(k)
        self.assertIsNone(result)

    def test_lru_eviction(self):
        cache = self._make_cache(max_size=2)
        k_a = self._CacheKey(query="a", categories="", language="", pageno=1)
        k_b = self._CacheKey(query="b", categories="", language="", pageno=1)
        k_c = self._CacheKey(query="c", categories="", language="", pageno=1)
        cache.put(k_a, [1])
        cache.put(k_b, [2])
        cache.put(k_c, [3])
        self.assertIsNone(cache.get(k_a))
        self.assertIsNotNone(cache.get(k_b))
        self.assertIsNotNone(cache.get(k_c))

    def test_ttl_expiration(self):
        cache = self._make_cache(ttl_s=0.1)
        k = self._CacheKey(query="ttl", categories="", language="", pageno=1)
        cache.put(k, [{"x": 1}])
        self.assertIsNotNone(cache.get(k))
        time.sleep(0.15)
        self.assertIsNone(cache.get(k))

    def test_thread_safety(self):
        cache = self._make_cache(max_size=100)
        errors = []

        def writer(start):
            try:
                for i in range(50):
                    k = self._CacheKey(query=f"s-{start}-{i}", categories="", language="", pageno=1)
                    cache.put(k, [i])
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for _ in range(50):
                    k = self._CacheKey(query="s-0-0", categories="", language="", pageno=1)
                    cache.get(k)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        threads += [threading.Thread(target=reader) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])

    def test_stats_property(self):
        cache = self._make_cache(max_size=5, ttl_s=10.0)
        k = self._CacheKey(query="s", categories="", language="", pageno=1)
        cache.put(k, [1])
        cache.get(k)  # hit
        cache.get(self._CacheKey(query="miss", categories="", language="", pageno=1))  # miss
        s = cache.stats
        self.assertEqual(s["size"], 1)
        self.assertEqual(s["max_size"], 5)
        self.assertEqual(s["hits"], 1)
        self.assertEqual(s["misses"], 1)
        self.assertEqual(s["ttl_s"], 10.0)

    def test_clear(self):
        cache = self._make_cache()
        k = self._CacheKey(query="c", categories="", language="", pageno=1)
        cache.put(k, [1])
        cache.clear()
        self.assertIsNone(cache.get(k))


class TestCacheKey(unittest.TestCase):

    def test_deterministic(self):
        from deerflow.community.searxng.search_cache import CacheKey
        k1 = CacheKey(query="python", categories="general", language="en", pageno=1)
        k2 = CacheKey(query="python", categories="general", language="en", pageno=1)
        self.assertEqual(k1.to_hash(), k2.to_hash())

    def test_different_queries(self):
        from deerflow.community.searxng.search_cache import CacheKey
        k1 = CacheKey(query="python", categories="general", language="en", pageno=1)
        k2 = CacheKey(query="java", categories="general", language="en", pageno=1)
        self.assertNotEqual(k1.to_hash(), k2.to_hash())

    def test_hash_length(self):
        from deerflow.community.searxng.search_cache import CacheKey
        k = CacheKey(query="test", categories="", language="", pageno=1)
        self.assertEqual(len(k.to_hash()), 32)


if __name__ == "__main__":
    unittest.main()
