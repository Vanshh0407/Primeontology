import copy
import json

from django.test import TestCase

from prime_ontology import agent, assistant, generator, mapping, model_ops, query, reasoning, validation, versioning
from prime_ontology.introspect import parse_sql_ddl

from .helpers import DEMO_SQL, jpost, jput


def demo_model():
    m = generator.generate_model(parse_sql_ddl(DEMO_SQL.read_text()))
    m["classes"].append({"name": "PremiumCustomer", "label": "Premium Customer", "comment": "", "parents": ["IndividualCustomer"]})
    m["classes"].append({"name": "IndividualCustomer", "label": "Individual Customer", "comment": "", "parents": ["Customer"]})
    m["individuals"] = [
        {"name": "initech", "class": "Customer", "data": {"customerName": "Initech"}, "links": {}},
        {"name": "order1", "class": "SalesOrder", "data": {}, "links": {"hasCustomer": "initech"}},
        {"name": "bolt", "class": "Product", "data": {"productName": "Bolt"}, "links": {"hasSupplier": "acme"}},
        {"name": "acme", "class": "Supplier", "data": {"supplierName": "Acme"}, "links": {}},
        {"name": "item1", "class": "SalesOrderItem", "data": {}, "links": {"hasOrder": "order1", "hasProduct": "bolt"}},
    ]
    return m


class ValidationTests(TestCase):
    def codes(self, m, **kw):
        return [i["code"] for i in validation.validate(m, **kw)["issues"]]

    def test_clean_model_scores_high(self):
        r = validation.validate(generator.generate_model(parse_sql_ddl(DEMO_SQL.read_text())))
        self.assertEqual(r["errors"], 0)
        self.assertGreaterEqual(r["health"], 90)
        self.assertGreater(r["passed"], 20)

    def test_detects_problems(self):
        m = demo_model()
        m["classes"] += [{"name": "customer", "label": "Customer", "parents": []}, {"name": "Lonely", "label": "Lonely", "parents": []},
                         {"name": "A", "parents": ["B"]}, {"name": "B", "parents": ["A"]}, {"name": "Self", "parents": ["Self"]},
                         {"name": "bad name!", "parents": []}]
        m["dataProperties"] += [{"name": "x", "domain": "Nope", "datatype": "string"}, {"name": "y", "domain": "Customer", "datatype": "wat"},
                                {"name": "z", "domain": None, "datatype": "string"},
                                {"name": "customerName", "domain": "Customer", "datatype": "string"}]
        m["objectProperties"] += [{"name": "r", "domain": "Customer", "range": "Ghost"},
                                  {"name": "c", "domain": "Customer", "range": "Product", "minCardinality": 3, "maxCardinality": 1}]
        codes = set(self.codes(m))
        for c in ("DUPLICATE_CLASS", "ORPHAN_CLASS", "CIRCULAR_HIERARCHY", "SELF_SUBCLASS", "INVALID_IRI", "INVALID_DOMAIN",
                  "INVALID_RANGE", "MISSING_DOMAIN", "CARDINALITY_CONFLICT", "DUPLICATE_PROPERTY"):
            self.assertIn(c, codes)
        self.assertLess(validation.validate(m)["health"], 50)

    def test_duplicate_concept_and_incompatible_range_and_mapping_conflict(self):
        m = demo_model()
        m["classes"].append({"name": "Customers", "label": "Customers", "parents": []})
        m["objectProperties"].append({"name": "hasCustomer", "domain": "PremiumCustomer", "range": "Supplier"})
        m["objectProperties"].append({"name": "hasCustomer", "domain": "Customer", "range": "Product"})
        maps = [{"source": "crm", "field": "n", "target": "Customer.a", "status": "accepted"},
                {"source": "crm", "field": "n", "target": "Ghost.b", "status": "accepted"}]
        codes = set(self.codes(m, mappings=maps))
        self.assertIn("DUPLICATE_CONCEPT", codes)
        self.assertIn("INCOMPATIBLE_RANGE", codes)
        self.assertIn("MAPPING_CONFLICT", codes)
        self.assertIn("MAPPING_TARGET_MISSING", codes)


class ReasoningTests(TestCase):
    def test_transitive_superclass(self):
        m = demo_model()
        inf = {(x["subject"], x["object"]) for x in reasoning.inferred_hierarchy(m)}
        self.assertIn(("PremiumCustomer", "Customer"), inf)

    def test_owlrl_and_individual_types(self):
        m = demo_model()
        m["individuals"].append({"name": "vip", "class": "PremiumCustomer", "data": {}, "links": {}})
        r = reasoning.reason(m)
        self.assertTrue(r["consistent"])
        self.assertGreater(r["triplesAfter"], r["triplesBefore"])
        self.assertTrue(any(t["individual"] == "vip" and "Customer" in t["inferred"] for t in r["inferredIndividualTypes"]))
        self.assertTrue(reasoning.reason(m, "rdfs")["consistent"])

    def test_disjoint_inconsistency(self):
        m = demo_model()
        for c in m["classes"]:
            if c["name"] == "Customer":
                c["disjointWith"] = ["Supplier"]
        m["individuals"].append({"name": "both", "class": "Customer", "data": {}, "links": {}})
        m["individuals"].append({"name": "both", "class": "Supplier", "data": {}, "links": {}})
        self.assertFalse(reasoning.reason(m)["consistent"])

    def test_shacl(self):
        m = demo_model()
        ok = reasoning.shacl_validate(m, None)
        self.assertIn("conforms", ok)
        ttl = f"""@prefix onto: <{generator.DEFAULT_BASE_IRI}> . @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
        onto:c1 a onto:Product ; onto:productName "P" ."""  # missing required unitPrice
        bad = reasoning.shacl_validate(m, ttl)
        self.assertFalse(bad["conforms"])
        self.assertTrue(any("unitPrice" in v["path"] for v in bad["violations"]), bad["violations"])
        self.assertIn("sh:NodeShape", reasoning.build_shapes(m).serialize(format="turtle"))


class QueryTests(TestCase):
    def setUp(self):
        self.m = demo_model()

    def test_search_and_concept(self):
        self.assertEqual(query.search(self.m, "customer")[0]["name"], "Customer")
        self.assertTrue(query.search(self.m, "ordr date"))
        c = query.concept(self.m, "SalesOrder")
        self.assertIn("hasCustomer", [r["name"] for r in c["relationships"]["outgoing"]])
        self.assertIn("hasOrder", [r["name"] for r in query.concept(self.m, "SalesOrder")["relationships"]["incoming"]])
        self.assertIn("Customer", query.concept(self.m, "PremiumCustomer")["ancestors"])
        self.assertTrue(query.concept(self.m, "Customer")["sources"])
        with self.assertRaises(KeyError):
            query.concept(self.m, "Nope")

    def test_path(self):
        p = query.find_path(self.m, "Customer", "Supplier")
        self.assertEqual([s["to"] for s in p], ["SalesOrder", "SalesOrderItem", "Product", "Supplier"])
        self.assertIsNone(query.find_path(self.m, "Customer", "Nope"))

    def test_sparql(self):
        r = query.run_sparql(self.m, "SELECT ?c WHERE { ?c a owl:Class } ORDER BY ?c")
        self.assertGreaterEqual(r["count"], 5)
        r = query.run_sparql(self.m, "ASK { ?s a onto:Customer }")
        self.assertTrue(r["rows"][0][0])
        for bad in ("INSERT DATA { <a> <b> <c> }", "DELETE WHERE { ?s ?p ?o }", "LOAD <http://x>", "DROP ALL", "hello",
                    "SELECT * WHERE { SERVICE <http://x> { ?s ?p ?o } }"):
            with self.assertRaises(ValueError, msg=bad):
                query.run_sparql(self.m, bad)
        self.assertEqual(query.run_sparql(self.m, 'SELECT * WHERE { ?s rdfs:label "INSERT" }')["type"], "SELECT")

    def test_natural_language_customers_orders_products_suppliers(self):
        r = query.natural_query(self.m, "Find customers who placed sales orders for products supplied by suppliers")
        self.assertEqual([c["class"] for c in r["interpretation"]], ["Customer", "SalesOrder", "Product", "Supplier"])
        self.assertEqual(r["result"]["count"], 1)
        row = dict(zip(r["result"]["columns"], r["result"]["rows"][0]))
        self.assertEqual(row["v0"], "onto:initech")
        self.assertIn("Supplier", r["explanation"])
        with self.assertRaises(ValueError):
            query.natural_query(self.m, "how is the weather")


class MappingTests(TestCase):
    def setUp(self):
        self.m = generator.generate_model(parse_sql_ddl(DEMO_SQL.read_text()))

    def best(self, res, src, field):
        return next(r for r in res["proposals"] if r["source"] == src and r["field"] == field)

    def test_heterogeneous_sources(self):
        res = mapping.propose_mappings([
            {"name": "CRM", "fields": [{"name": "customer_name", "type": "varchar"}]},
            {"name": "ERP", "fields": [{"name": "cust_nm", "type": "varchar"}]},
            {"name": "clients.csv", "fields": [{"name": "client_name", "type": "varchar"}, {"name": "zzz_unrelated_qq", "type": "varchar"}]},
            {"name": "Contract", "fields": [{"name": "Buyer", "type": None}]},
            {"name": "orders", "fields": [{"name": "order_dt", "type": "date"}, {"name": "unit_prc", "type": "numeric"}]}], self.m)
        for src, f in (("CRM", "customer_name"), ("ERP", "cust_nm"), ("clients.csv", "client_name")):
            b = self.best(res, src, f)
            self.assertEqual(b["target"], "Customer.customerName", (src, f, b["candidates"]))
        self.assertEqual(self.best(res, "ERP", "cust_nm")["best"]["confidence"], "HIGH")
        self.assertEqual(self.best(res, "Contract", "Buyer")["target"], "Customer")
        self.assertEqual(self.best(res, "orders", "order_dt")["target"], "SalesOrder.orderDate")
        self.assertEqual(self.best(res, "clients.csv", "zzz_unrelated_qq")["status"], "unmapped")
        self.assertTrue(any(d["target"] == "Customer.customerName" and len(d["fields"]) == 3 for d in res["duplicates"]))
        self.assertTrue(self.best(res, "ERP", "cust_nm")["best"]["reasons"])

    def test_datatype_influences_score(self):
        a = mapping.propose_mappings([{"name": "t", "fields": [{"name": "unit_price", "type": "numeric"}]}], self.m)["proposals"][0]
        b = mapping.propose_mappings([{"name": "t", "fields": [{"name": "unit_price", "type": "boolean"}]}], self.m)["proposals"][0]
        self.assertGreater(a["score"], b["score"])

    def test_commit_adds_lineage_only_for_accepted(self):
        maps = [{"source": "ERP", "field": "cust_nm", "target": "Customer.customerName", "status": "accepted", "score": .97},
                {"source": "X", "field": "y", "target": "Customer.customerType", "status": "rejected"}]
        m2, n = mapping.commit_mappings(self.m, maps, "set1")
        self.assertEqual(n, 1)
        self.assertEqual(next(p for p in m2["dataProperties"] if p["name"] == "customerName")["mappedFrom"][0]["field"], "cust_nm")
        self.assertNotIn("mappedFrom", next(p for p in self.m["dataProperties"] if p["name"] == "customerName"))


class AssistantTests(TestCase):
    def test_template_proposal_and_never_silent(self):
        empty = model_ops.empty_model()
        p = assistant.propose(empty, "Create an ontology for contract management", use_llm=False)
        names = {o["name"] for o in p["ops"] if o["op"] == "add_class"}
        self.assertTrue({"Contract", "Party", "Obligation", "Clause", "Payment"} <= names)
        self.assertEqual(empty["classes"], [])  # proposal did not mutate
        after = assistant.apply_operations(empty, p["ops"])
        self.assertIn("Contract", {c["name"] for c in after["classes"]})
        self.assertIn(("Contract", "hasParty", "Party"), {(r["domain"], r["name"], r["range"]) for r in after["objectProperties"]})

    def test_commands(self):
        m = demo_model()
        ops = assistant.propose(m, "Connect SalesOrder to Supplier as suppliedVia", use_llm=False)["ops"]
        self.assertEqual(ops[0]["name"], "suppliedVia")
        ops = assistant.propose(m, "Add class Vehicle under Product. Rename Customer to Client. Add property price to Vehicle as decimal", use_llm=False)["ops"]
        after = assistant.apply_operations(m, ops)
        self.assertIn("Vehicle", {c["name"] for c in after["classes"]})
        self.assertIn("Client", {c["name"] for c in after["classes"]})
        self.assertNotIn("Customer", {c["name"] for c in after["classes"]})
        self.assertTrue(any(r["range"] == "Client" for r in after["objectProperties"]))
        self.assertEqual(assistant.propose(m, "blah", use_llm=False)["ops"], [])

    def test_invalid_ops_dropped(self):
        m = demo_model()
        ops = assistant.normalize_ops(m, [{"op": "add_class", "name": "bad name"}, {"op": "delete_class", "name": "Ghost"},
                                          {"op": "evil"}, {"op": "add_class", "name": "Good"}])
        self.assertEqual(ops, [{"op": "add_class", "name": "Good"}])


class AgentTests(TestCase):
    def setUp(self):
        self.m = demo_model()
        self.m["agentic"] = {"tools": [
            {"name": "crm_lookup", "type": "api", "concepts": ["Customer"], "operations": ["read"], "endpoint": "/crm"},
            {"name": "order_service", "type": "mcp", "concepts": ["SalesOrder"], "operations": ["read", "create"]},
            {"name": "supplier_db", "type": "function", "concepts": ["Supplier"], "operations": ["read"]}],
            "policies": [{"name": "no-delete-orders", "effect": "deny", "concepts": ["SalesOrder"], "operations": ["delete"]},
                         {"name": "approve-supplier-writes", "effect": "require_approval", "concepts": ["Supplier"], "operations": ["update"]}],
            "rules": []}

    def test_plan_binds_tools_along_path(self):
        p = agent.plan(self.m, "Find customers who placed sales orders")
        self.assertEqual(p["decision"], "ready")
        self.assertEqual([s["tool"]["name"] for s in p["steps"]], ["crm_lookup", "order_service"])

    def test_policy_deny_and_approval(self):
        self.assertEqual(agent.plan(self.m, "Delete the sales order")["decision"], "blocked")
        self.assertEqual(agent.plan(self.m, "Update supplier details")["decision"], "needs_approval")

    def test_partial_and_unreachable_and_ungrounded(self):
        p = agent.plan(self.m, "Find customers and their products")
        self.assertEqual(p["decision"], "partial")
        self.assertIn("Product", p["unboundConcepts"])
        self.assertEqual(agent.plan(self.m, "weather today")["decision"], "clarify")
        self.m["classes"].append({"name": "Island", "parents": []})
        self.assertEqual(agent.plan(self.m, "customers and island")["decision"], "unreachable")

    def test_registry_validation_and_context_pack(self):
        bad = {"tools": [{"name": "t", "concepts": ["Ghost"]}], "policies": [{"name": "p", "effect": "maybe"}]}
        self.assertEqual(len(agent.validate_registry(self.m, bad)), 2)
        pack = agent.context_pack(self.m, "customers and orders")
        self.assertIn("SalesOrder", pack["text"])
        self.assertIn("no-delete-orders", pack["text"])


class VersioningTests(TestCase):
    def setUp(self):
        self.c = self.client
        self.oid = jpost(self.c, "/api/v1/ontology/", {"name": "Gov", "model": demo_model()}).json()["id"]
        self.base = f"/api/v1/ontology/{self.oid}"

    def hdr(self, user, role):
        return {"HTTP_X_PRIME_USER": user, "HTTP_X_PRIME_ROLE": role}

    def test_full_workflow_and_diff_and_rollback(self):
        v1 = jpost(self.c, f"{self.base}/versions/", {"message": "initial"}, **self.hdr("alice", "editor")).json()
        self.assertEqual(v1["number"], "1.0")
        m = self.c.get(self.base + "/").json()["model"]
        m["classes"].append({"name": "Warehouse", "label": "Warehouse", "parents": []})
        m["classes"] = [c for c in m["classes"] if c["name"] != "IndividualCustomer"]
        jput(self.c, self.base + "/", {"model": m}, **self.hdr("alice", "editor"))
        d = self.c.get(self.base + "/diff/").json()
        self.assertIn("+ Class: Warehouse", d["lines"])
        self.assertIn("- Class: IndividualCustomer", d["lines"])
        v2 = jpost(self.c, f"{self.base}/versions/", {"message": "add warehouse"}, **self.hdr("alice", "editor")).json()
        self.assertEqual(v2["number"], "1.1")
        act = lambda n, a, u, r: jpost(self.c, f"{self.base}/versions/{n}/{a}/", **self.hdr(u, r))
        self.assertEqual(act("1.1", "publish", "bob", "admin").status_code, 400)  # not approved yet
        self.assertEqual(act("1.1", "submit", "alice", "viewer").status_code, 403)
        self.assertEqual(act("1.1", "submit", "alice", "editor").status_code, 200)
        self.assertEqual(act("1.1", "approve", "alice", "reviewer").status_code, 400)  # four-eyes
        self.assertEqual(act("1.1", "approve", "carol", "editor").status_code, 403)  # role
        self.assertEqual(act("1.1", "approve", "carol", "reviewer").status_code, 200)
        self.assertEqual(act("1.1", "publish", "carol", "reviewer").status_code, 403)
        self.assertEqual(act("1.1", "publish", "dan", "admin").status_code, 200)
        o = self.c.get(self.base + "/").json()
        self.assertEqual((o["status"], o["currentVersion"]), ("published", "1.1"))
        # edit after publish reopens as draft
        m["classes"].append({"name": "Extra", "label": "Extra", "parents": []})
        jput(self.c, self.base + "/", {"model": m}, **self.hdr("alice", "editor"))
        self.assertEqual(self.c.get(self.base + "/").json()["status"], "draft")
        # rollback to 1.0 restores IndividualCustomer, creates new version
        rb = act("1.0", "rollback", "dan", "admin")
        self.assertEqual(rb.status_code, 200)
        names = {c["name"] for c in self.c.get(self.base + "/").json()["model"]["classes"]}
        self.assertIn("IndividualCustomer", names)
        self.assertNotIn("Warehouse", names)
        self.assertEqual(act("1.0", "rollback", "x", "editor").status_code, 403)
        # publishing a newer version archives the old one
        vs = {v["number"]: v["status"] for v in self.c.get(self.base + "/versions/").json()["results"]}
        self.assertEqual(vs["1.1"], "published")
        actions = [e["action"] for e in self.c.get(self.base + "/audit/").json()["results"]]
        for a in ("version.commit", "version.submit", "version.approve", "version.publish", "version.rollback", "model.update"):
            self.assertIn(a, actions)

    def test_diff_rename_and_changed(self):
        a = demo_model()
        b = model_ops.rename_class(a, "Customer", "Client")
        b["dataProperties"][0]["datatype"] = "long"
        d = versioning.diff(a, b)
        self.assertIn({"from": "Customer", "to": "Client"}, d["classes"]["renamed"])
        self.assertTrue(any("datatype" in l or "Customer" in l for l in d["lines"]))

    def test_rbac_read_only_viewer_and_tenant(self):
        v = self.hdr("v", "viewer")
        self.assertEqual(self.c.get(self.base + "/", **v).status_code, 200)
        self.assertEqual(jput(self.c, self.base + "/", {"name": "x"}, **v).status_code, 403)
        self.assertEqual(self.c.delete(self.base + "/", **v).status_code, 403)
        t = {"HTTP_X_PRIME_TENANT": "other"}
        self.assertEqual(self.c.get(self.base + "/", **t).status_code, 404)
        self.assertEqual(self.c.get("/api/v1/ontology/", **t).json()["results"], [])


class EmbeddedAndAiApiTests(TestCase):
    def setUp(self):
        self.oid = jpost(self.client, "/api/v1/ontology/", {"name": "E", "model": demo_model()}).json()["id"]
        self.base = f"/api/v1/ontology/{self.oid}"

    def test_embedded_contract(self):
        man = self.client.get("/api/v1/ontology/embedded/manifest/").json()
        self.assertEqual(man["hosts"], ["primeagenticos", "primesemonto", "unicontractai"])
        for host in man["hosts"]:
            c = self.client.get(f"/api/v1/ontology/embedded/context/?host={host}&ontology_id={self.oid}").json()
            self.assertEqual(c["host"], host)
            self.assertIn("vocabularyCoverage", c)
        self.assertEqual(self.client.get("/api/v1/ontology/embedded/context/?host=nope").status_code, 404)
        ok = jpost(self.client, "/api/v1/ontology/embedded/event/", {"type": "concept.selected", "host": "primesemonto", "ontologyId": self.oid, "payload": {"c": "Customer"}})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(jpost(self.client, "/api/v1/ontology/embedded/event/", {"type": "x"}).status_code, 400)

    def test_ai_proposal_then_approval(self):
        r = jpost(self.client, f"{self.base}/ai/", {"prompt": "Add class Warranty", "useLlm": False}).json()
        self.assertTrue(r["requiresApproval"])
        self.assertIn("+ Class: Warranty", r["diff"]["lines"])
        before = self.client.get(self.base + "/").json()["model"]
        self.assertNotIn("Warranty", {c["name"] for c in before["classes"]})  # not silently changed
        ap = jpost(self.client, f"{self.base}/ai/apply/", {"ops": r["ops"]})
        self.assertIn("Warranty", {c["name"] for c in ap.json()["model"]["classes"]})

    def test_end_to_end_endpoints(self):
        g = lambda p: self.client.get(f"{self.base}{p}")
        self.assertEqual(g("/validate/").status_code, 200)
        self.assertEqual(g("/reason/").status_code, 200)
        self.assertEqual(g("/reason/?profile=bad").status_code, 400)
        self.assertIn("NodeShape", g("/shapes/").content.decode())
        self.assertEqual(jpost(self.client, f"{self.base}/shacl/", {}).status_code, 200)
        self.assertGreater(len(g("/graph/").json()["edges"]), 3)
        self.assertEqual(g("/search/?q=supplier").json()["results"][0]["name"], "Supplier")
        self.assertEqual(g("/concepts/Customer/").status_code, 200)
        self.assertEqual(g("/concepts/Nope/").status_code, 404)
        self.assertTrue(g("/path/?from=Customer&to=Supplier").json()["connected"])
        q = jpost(self.client, f"{self.base}/query/", {"kind": "natural", "text": "customers who placed sales orders"})
        self.assertEqual(q.status_code, 200)
        self.assertEqual(jpost(self.client, f"{self.base}/query/", {"kind": "sparql", "text": "DROP ALL"}).status_code, 400)
        sq = jpost(self.client, f"{self.base}/queries/", {"name": "all", "text": "SELECT * WHERE {?s ?p ?o}"})
        self.assertEqual(sq.status_code, 201)
        self.assertEqual(len(g("/queries/?saved=1").json()["results"]), 1)
        self.assertGreaterEqual(len(g("/queries/").json()["results"]), 2)  # history
        self.assertEqual(self.client.delete(f"{self.base}/queries/{sq.json()['id']}/").status_code, 204)
        reg = {"tools": [{"name": "t", "concepts": ["Customer"], "operations": ["read"]}], "policies": []}
        self.assertEqual(jput(self.client, f"{self.base}/agentic/", reg).status_code, 200)
        self.assertEqual(jput(self.client, f"{self.base}/agentic/", {"tools": [{"name": "t", "concepts": ["Ghost"]}]}).status_code, 400)
        p = jpost(self.client, f"{self.base}/agent/plan/", {"request": "find customers"}).json()
        self.assertEqual(p["steps"][0]["tool"]["name"], "t")
        self.assertIn("Customer", g("/agent/context/?q=customers").json()["text"])
        for fmt in ("turtle", "xml", "json-ld", "nt"):
            self.assertEqual(g(f"/export/?format={fmt}").status_code, 200)
        self.assertEqual(g("/export/?format=bad").status_code, 400)

    def test_mapping_api_flow(self):
        r = jpost(self.client, f"{self.base}/mapping/propose/", {"sources": [{"name": "ERP", "fields": [{"name": "cust_nm", "type": "varchar"}]}]}).json()
        self.assertEqual(r["proposals"][0]["target"], "Customer.customerName")
        row = {**r["proposals"][0], "status": "accepted"}
        ms = jpost(self.client, f"{self.base}/mappings/", {"name": "s1", "mappings": [row]}).json()
        c = jpost(self.client, f"{self.base}/mappings/{ms['id']}/commit/").json()
        self.assertEqual(c["committed"], 1)
        self.assertTrue(self.client.get(f"{self.base}/concepts/Customer/").json()["mappings"])
        self.assertEqual(jpost(self.client, f"{self.base}/mapping/propose/", {"sources": []}).status_code, 400)
        self.assertEqual(self.client.delete(f"{self.base}/mappings/{ms['id']}/").status_code, 204)
