import base64
import json

from django.test import TestCase, override_settings

from prime_ontology import embeddings
from prime_ontology.fabric.sync import sync_source
from prime_ontology.rag import ingest as rag_ingest
from prime_ontology.rag import retrieve as rr
from prime_ontology.rag.answer import ABSTAIN, answer
from prime_ontology.rag.models import RagChunk

from .fabric_helpers import make_ontology, make_source

H = {"HTTP_X_PRIME_USER": "u"}

SUP = "id,name\nS1,Acme Steel\nS2,Borealis Metals\nS3,Cobalt Works\n"
INV = "id,supplier,days,amount\nI1,S1,75,1000\nI2,S1,10,500\nI3,S2,30,900\nI4,S3,90,300\n"
CON = "id,title,supplier,invoice\nC1,Steel Supply 2026,S1,I1\nC2,Metals Framework,S2,I3\nC3,Cobalt Services,S3,I4\nC4,Old Deal,S2,I3\n"

CONTRACT_DOC = """# Payment terms
Acme Steel must be paid within 30 days of invoice. Late payments incur a 2% monthly penalty.

# Termination
Either party may terminate the Steel Supply 2026 contract with 90 days written notice.
"""


def build():
    o = make_ontology()
    ident = []
    make_source(o, "ERP-S", SUP, spec={"class": "Supplier", "id": "id", "fields": {"name": "supplierName"}, "identity": ident})
    make_source(o, "ERP-I", INV, spec={"class": "Invoice", "id": "id", "fields": {"days": "overdueDays", "amount": "amount"},
                                       "identity": ident})
    make_source(o, "ERP-C", CON, spec={"class": "Contract", "id": "id", "fields": {"title": "title"}, "identity": ident,
                                        "relations": [{"field": "supplier", "target": "Supplier", "property": "hasSupplier"},
                                                      {"field": "invoice", "target": "Invoice", "property": "hasInvoice"}]})
    for s in o.fabric_sources.all().order_by("id"):
        r = sync_source(s)
        assert r.status == "succeeded", (s.name, r.error)
    return o


@override_settings(PRIME_ONTOLOGY_EMBEDDINGS="hash")
class RagTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        embeddings.reset()
        cls.o = build()
        rag_ingest.ingest_document(cls.o, title="Steel Supply Agreement", text=CONTRACT_DOC, doc_type="contract", metadata={"dept": "legal"})
        rag_ingest.ingest_document(cls.o, title="Board minutes", text="# Q3\nThe board discussed hiring plans for the new office.", doc_type="minutes",
                                   classification="restricted")

    def setUp(self):
        rr.invalidate(self.o.id)

    def test_chunking_respects_sections_and_tags_concepts_and_entities(self):
        chunks = list(RagChunk.objects.filter(document__title="Steel Supply Agreement").order_by("ordinal"))
        self.assertEqual([c.heading for c in chunks], ["Payment terms", "Termination"])
        self.assertIn("Supplier", chunks[0].concepts)
        self.assertTrue(chunks[0].entities)  # 'Acme Steel' is a fabric entity
        self.assertTrue(all(c.embedding for c in chunks))

    def test_long_sections_are_split_with_overlap(self):
        words = " ".join(f"w{i}." if i % 17 == 0 else f"w{i}" for i in range(400))
        chunks = rag_ingest.chunk_sections([{"heading": "H", "page": None, "text": words}])
        self.assertGreater(len(chunks), 2)
        self.assertTrue(all(len(c["text"].split()) <= rag_ingest.TARGET_WORDS for c in chunks))
        a, b = chunks[0]["text"].split(), chunks[1]["text"].split()
        self.assertTrue(set(a[-10:]) & set(b[:30]))  # overlap

    def test_reingest_same_content_is_a_no_op_and_changed_content_replaces_chunks(self):
        d = rag_ingest.ingest_document(self.o, title="Tmp", text="# A\nfirst version text here")
        n = RagChunk.objects.filter(ontology=self.o).count()
        rag_ingest.ingest_document(self.o, title="Tmp", text="# A\nfirst version text here")
        self.assertEqual(RagChunk.objects.filter(ontology=self.o).count(), n)
        rag_ingest.ingest_document(self.o, title="Tmp", text="# A\nsecond version completely different")
        self.assertFalse(RagChunk.objects.filter(document=d, text__contains="first version").exists())
        d.refresh_from_db()
        d.delete()

    def test_the_documents_example_structured_graph_question(self):
        res = rr.retrieve(self.o, "contracts with suppliers whose invoices were overdue more than 60 days")
        s = res["structured"]
        self.assertEqual(s["resultClass"], "Contract")
        self.assertEqual(s["constraints"], [{"class": "Invoice", "property": "overdueDays", "op": "gt", "value": 60}])
        titles = sorted(res_c["title"] for res_c in res["context"] if res_c["kind"] == "entity" and res_c["class"] == "Contract")
        self.assertEqual(titles, ["Cobalt Services", "Steel Supply 2026"])  # C2/C4 only reach a 30-day invoice
        self.assertEqual(s["total"], 2)
        self.assertEqual(res["confidence"]["label"], "high")
        self.assertTrue(all(m["evidence"] for m in s["matches"]))

    def test_threshold_direction_and_other_operators(self):
        lt = rr.retrieve(self.o, "contracts with invoices overdue less than 20 days")["structured"]
        self.assertEqual(lt["constraints"][0]["op"], "lt")
        self.assertEqual(lt["total"], 0)  # I2 (10 days) belongs to no contract
        gte = rr.retrieve(self.o, "contracts with invoices overdue at least 30 days")["structured"]
        self.assertEqual(gte["total"], 4)

    def test_text_retrieval_expansion_and_citations(self):
        out = answer(self.o, "How long is the notice period to terminate the steel supply contract?")
        self.assertFalse(out["abstained"])
        self.assertIn("90 days", out["answer"])
        self.assertEqual(out["grounding"] in ("grounded", "partially_grounded"), True)
        self.assertTrue(out["citations"])
        self.assertTrue(any(c["title"] == "Steel Supply Agreement" and c["heading"] == "Termination" for c in out["citations"]))
        self.assertIn("lexical", out["signals"])

    def test_graph_signal_entity_named_in_question_pulls_in_its_facts_and_provenance(self):
        res = rr.retrieve(self.o, "What do we know about Acme Steel?")
        ents = [c for c in res["context"] if c["kind"] == "entity"]
        self.assertTrue(any(e["title"] == "Acme Steel" and e["sources"] == ["ERP-S"] for e in ents))
        self.assertTrue(res["seedEntities"])
        self.assertIn("graph", res["signals"])

    def test_abstains_when_there_is_no_evidence(self):
        out = answer(self.o, "What is the airspeed velocity of an unladen swallow?")
        self.assertTrue(out["abstained"])
        self.assertEqual(out["answer"], ABSTAIN)
        self.assertEqual(out["grounding"], "ungrounded")
        self.assertEqual(out["citations"], [])

    def test_classification_filtering_by_role(self):
        viewer = rr.retrieve(self.o, "board hiring plans new office", role="viewer")
        self.assertFalse(any("hiring" in c["text"] for c in viewer["context"] if c["kind"] == "chunk"))
        self.assertEqual(viewer["withheldByPolicy"], 1)
        admin = rr.retrieve(self.o, "board hiring plans new office", role="admin")
        self.assertTrue(any("hiring" in c["text"] for c in admin["context"] if c["kind"] == "chunk"))

    def test_metadata_filters_and_context_size_control(self):
        none = rr.retrieve(self.o, "payment terms penalty", filters={"metadata": {"dept": "finance"}})
        self.assertFalse([c for c in none["context"] if c["kind"] == "chunk"])
        some = rr.retrieve(self.o, "payment terms penalty", filters={"metadata": {"dept": "legal"}, "docType": "contract"})
        self.assertTrue([c for c in some["context"] if c["kind"] == "chunk"])
        small = rr.retrieve(self.o, "payment terms penalty late payments", max_context_chars=500, k=10)
        self.assertLessEqual(small["contextChars"], 500 + 10)

    def test_llm_answers_are_verified_unsupported_and_uncited_sentences_are_dropped(self):
        import prime_ontology.rag.answer as A

        import re

        def fake(system, user, **k):
            n = re.search(r"\[(\d+)\] \(chunk[^)]*\) Acme Steel must be paid", user).group(1)
            return (f"Late payments incur a 2% monthly penalty [{n}]. The vendor also gives a 40% volume rebate [{n}]. "
                    "Acme Steel is the best supplier in the world. Payment is due within 30 days [99].")

        orig_avail, orig_complete = A.llm.available, A.llm.complete
        A.llm.available, A.llm.complete = (lambda: True), fake
        try:
            out = answer(self.o, "What happens with late payments by Acme Steel?", use_llm=True)
        finally:
            A.llm.available, A.llm.complete = orig_avail, orig_complete
        self.assertEqual(out["mode"], "llm")
        self.assertEqual([s["text"] for s in out["sentences"]], ["Late payments incur a 2% monthly penalty."])
        self.assertEqual(out["verification"]["droppedUnsupported"], 1)
        self.assertEqual(out["verification"]["droppedUncited"], 2)  # no citation, and a citation to a context item that does not exist
        self.assertEqual(out["grounding"], "partially_grounded")

    def test_llm_failure_falls_back_to_extractive(self):
        import prime_ontology.rag.answer as A

        def boom(*a, **k):
            raise RuntimeError("down")

        orig_avail, orig_complete = A.llm.available, A.llm.complete
        A.llm.available, A.llm.complete = (lambda: True), boom
        try:
            out = answer(self.o, "notice period to terminate the steel supply contract", use_llm=True)
        finally:
            A.llm.available, A.llm.complete = orig_avail, orig_complete
        self.assertIn("extractive", out["mode"])
        self.assertIn("90 days", out["answer"])

    def test_document_upload_file_and_api(self):
        H2 = {**H, "HTTP_X_PRIME_ROLE": "editor"}
        base = f"/api/v1/ontology/{self.o.id}/rag"
        post = lambda path, data, h=H2: self.client.post(f"{base}/{path}", data=json.dumps(data), content_type="application/json", **h)  # noqa: E731
        r = post("documents/", {"title": "Notes", "filename": "n.md", "contentBase64": base64.b64encode(b"# Shipping\nGoods ship from Pune every Monday.").decode()})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(post("documents/", {"title": "x", "filename": "n.exe", "contentBase64": "AAAA"}).status_code, 400)
        self.assertEqual(post("documents/", {"title": "x", "text": "t", "classification": "restricted"}).status_code, 403)  # editors cannot add restricted
        self.assertEqual(self.client.post(f"{base}/documents/", data=json.dumps({"title": "x", "text": "t"}), content_type="application/json",
                                          **{**H, "HTTP_X_PRIME_ROLE": "viewer"}).status_code, 403)
        a = post("ask/", {"question": "Where do goods ship from?"}).json()
        self.assertIn("Pune", a["answer"])
        self.assertEqual(post("ask/", {"question": ""}).status_code, 400)
        self.assertEqual(post("retrieve/", {"question": "ship", "filters": "bad"}).status_code, 400)
        docs = self.client.get(f"{base}/documents/", **H2).json()
        self.assertTrue(any(d["title"] == "Notes" for d in docs["documents"]))
        self.assertFalse(any(d["title"] == "Board minutes" for d in docs["documents"]))  # restricted: invisible to editors
        self.assertEqual(self.client.get(f"{base}/documents/", **{**H, "HTTP_X_PRIME_ROLE": "admin"}).json()["documents"].__len__() >= 3, True)
        self.assertEqual(self.client.post(f"{base}/documents/", data="{}", content_type="application/json", **{**H2, "HTTP_X_PRIME_TENANT": "zz"}).status_code, 404)

    def test_reindex_relinks_entities_after_fabric_changes(self):
        c = RagChunk.objects.filter(document__title="Steel Supply Agreement").first()
        c.entities = []
        c.save()
        rag_ingest.reindex(self.o, vectors=False)
        c.refresh_from_db()
        self.assertTrue(c.entities)
