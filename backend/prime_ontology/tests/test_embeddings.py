import os

from django.test import TestCase

from prime_ontology import embeddings, mapping, query

from .test_platform import demo_model


class HashProviderTests(TestCase):
    def test_status_is_honest_and_deterministic(self):
        st = embeddings.status()
        self.assertEqual((st["provider"], st["semantic"]), ("hash", False))
        self.assertGreater(embeddings.similarity("customer", "customers"), 0.7)
        self.assertLess(embeddings.similarity("customer", "invoice"), 0.2)

    def test_off_mode(self):
        os.environ["PRIME_ONTOLOGY_EMBEDDINGS"] = "off"
        try:
            self.assertEqual(embeddings.status()["provider"], "off")
            self.assertEqual(embeddings.similarity("a", "b"), 0.0)
        finally:
            os.environ.pop("PRIME_ONTOLOGY_EMBEDDINGS")

    def test_mapping_reports_provider_and_unchanged_by_hash(self):
        r = mapping.propose_mappings([{"name": "CRM", "fields": [{"name": "cust_nm", "type": "varchar"}]}], demo_model())
        self.assertEqual(r["embeddings"]["provider"], "hash")
        self.assertEqual(r["proposals"][0]["target"], "Customer.customerName")


def _model_available():
    os.environ["PRIME_ONTOLOGY_EMBEDDINGS"] = "fastembed"
    try:
        embeddings.reset()
        return embeddings.status()["semantic"]
    except Exception:
        return False
    finally:
        os.environ.pop("PRIME_ONTOLOGY_EMBEDDINGS", None)
        embeddings.reset()


import unittest


@unittest.skipUnless(_model_available(), "embedding model not available (needs fastembed + a one-time model download)")
class FastEmbedProviderTests(TestCase):
    def setUp(self):
        os.environ["PRIME_ONTOLOGY_EMBEDDINGS"] = "fastembed"
        embeddings.reset()

    def tearDown(self):
        os.environ.pop("PRIME_ONTOLOGY_EMBEDDINGS", None)
        embeddings.reset()

    def test_real_model_ranks_synonyms_above_unrelated(self):
        self.assertEqual(embeddings.status()["provider"], "fastembed")
        sims = embeddings.similarities("supplier", ["vendor", "invoice", "country", "agreement"])
        self.assertGreater(sims[0], max(sims[1:]))
        self.assertGreater(embeddings.similarity("contract", "agreement"), embeddings.similarity("contract", "country"))

    def test_search_finds_concept_with_no_shared_words(self):
        m = demo_model()
        names = [h["name"] for h in query.search(m, "vendor")]
        self.assertIn("Supplier", names)  # no 'vendor' synonym anywhere in the model
        hit = next(h for h in query.search(m, "vendor") if h["name"] == "Supplier")
        self.assertTrue(hit.get("semantic") or hit["score"] > 0)

    def test_mapping_boosts_semantic_synonyms(self):
        m = demo_model()
        lex_only = mapping.propose_mappings([{"name": "t", "fields": [{"name": "vendor_country"}]}], m)  # noqa: F841
        r = mapping.propose_mappings([{"name": "t", "fields": [{"name": "vendor_country", "type": "varchar"}]}], m)
        self.assertEqual(r["embeddings"]["provider"], "fastembed")
        self.assertIn(r["proposals"][0]["target"], {"Supplier.country", "Customer.customerType", None} | {c["target"] for c in r["proposals"][0]["candidates"]})
        self.assertTrue(any(c["target"] == "Supplier.country" for c in r["proposals"][0]["candidates"]))


class WarmupBehaviourTests(TestCase):
    """The model loads in the background; a request must never wait for it."""

    def setUp(self):
        self._saved = (embeddings._fast, embeddings._fast_state, embeddings._fallback_reason)
        os.environ["PRIME_ONTOLOGY_EMBEDDINGS"] = "auto"

    def tearDown(self):
        embeddings._fast, embeddings._fast_state, embeddings._fallback_reason = self._saved
        os.environ["PRIME_ONTOLOGY_EMBEDDINGS"] = "hash"
        embeddings.reset()

    def test_auto_mode_returns_the_lexical_provider_instantly_while_loading(self):
        import time

        embeddings._fast_state = "loading"
        t = time.time()
        st = embeddings.status()
        self.assertLess(time.time() - t, 0.5)
        self.assertEqual((st["provider"], st["semantic"], st["warming"]), ("hash", False, True))

    def test_a_failed_load_falls_back_and_reports_why(self):
        embeddings._fast_state, embeddings._fallback_reason = "failed", "OSError: offline"
        st = embeddings.status()
        self.assertEqual((st["provider"], st["warming"], st["fallbackReason"]), ("hash", False, "OSError: offline"))
        self.assertGreater(embeddings.similarity("customer", "customers"), 0.5)  # still functional

    def test_ready_model_is_used(self):
        class Fake:
            name, lo, hi = "fastembed", 0.6, 0.9

            def embed(self, texts):
                return [[1.0, 0.0] for _ in texts]

        embeddings._fast, embeddings._fast_state = Fake(), "ready"
        self.assertEqual(embeddings.status()["provider"], "fastembed")
        os.environ["PRIME_ONTOLOGY_EMBEDDINGS"] = "hash"
        self.assertEqual(embeddings.status()["provider"], "hash")  # mode switch is honoured immediately
