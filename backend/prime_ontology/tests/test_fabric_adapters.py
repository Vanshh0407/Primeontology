from datetime import datetime, timezone

from django.test import TestCase, override_settings

from prime_ontology.fabric import crypto
from prime_ontology.fabric.adapters import ADAPTERS, MySqlAdapter, OdooAdapter, RestAdapter, SalesforceAdapter, SapODataAdapter, build_adapter
from prime_ontology.fabric.http import ConnectorError
from prime_ontology.fabric.models import FabricEvent, FabricRecord, FabricSource
from prime_ontology.fabric.sync import sync_source

from . import mock_enterprise as M
from .fabric_helpers import make_ontology
from .test_fabric_core import entities

SINCE = datetime(2026, 2, 1, tzinfo=timezone.utc)
ALLOW = {"PRIME_ONTOLOGY_CONNECTOR_HOSTS": ["127.0.0.1"], "PRIME_ONTOLOGY_ALLOW_HTTP_URLS": True}


class EnterpriseBase(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mock = M.MockEnterprise()

    @classmethod
    def tearDownClass(cls):
        cls.mock.stop()
        super().tearDownClass()

    def setUp(self):
        self.mock.state.__init__()  # fresh data + call log for every test
        self._o = override_settings(**ALLOW)
        self._o.enable()

    def tearDown(self):
        self._o.disable()

    def calls(self, prefix=""):
        return [c for c in self.mock.state.calls if c["path"].startswith(prefix)]


class SapTests(EnterpriseBase):
    def sap(self, **kw):
        return SapODataAdapter({"base_url": self.mock.base + "/sap/opu/odata/sap/API_BUSINESS_PARTNER", **kw}, {"user": M.SAP_USER, "password": M.SAP_PASS})

    def test_v2_connect_paging_and_sap_dates(self):
        a = self.sap()
        self.assertTrue(a.test()["ok"])
        rows = list(a.fetch({"entity_set": "A_BusinessPartner", "page_size": 2}))
        self.assertEqual(len(rows), 3)  # two pages: __next followed
        self.assertEqual(rows[0]["LastChangeDate"], "2026-01-10T10:00:00Z")  # /Date(ms)/ converted
        self.assertNotIn("__metadata", rows[0])
        self.assertEqual(len([c for c in self.calls("/sap/") if "A_BusinessPartner" in c["path"]]), 2)

    def test_v2_incremental_filter(self):
        rows = list(self.sap().fetch({"entity_set": "A_BusinessPartner", "updated_at_field": "LastChangeDate"}, since=SINCE))
        self.assertEqual([r["BusinessPartner"] for r in rows], ["0000100124", "0000100125"])
        q = self.calls("/sap/")[-1]["query"]["$filter"][0]
        self.assertEqual(q, "LastChangeDate gt datetime'2026-02-01T00:00:00'")

    def test_v4_uses_bearer_value_array_and_odata_filter(self):
        a = SapODataAdapter({"base_url": self.mock.base + "/odata4", "version": "v4"}, {"bearer": "sap4-token"})
        rows = list(a.fetch({"entity_set": "BusinessPartners", "updated_at_field": "LastChangeDate", "page_size": 2}, since=SINCE))
        self.assertEqual(len(rows), 2)
        self.assertEqual(self.calls("/odata4/")[0]["query"]["$filter"][0], "LastChangeDate gt 2026-02-01T00:00:00Z")
        with self.assertRaises(ConnectorError):
            list(SapODataAdapter({"base_url": self.mock.base + "/odata4", "version": "v4"}, {"bearer": "wrong"}).fetch({"entity_set": "BusinessPartners"}))

    def test_wrong_password_error_never_leaks_credentials_even_when_the_server_echoes_them(self):
        bad = SapODataAdapter({"base_url": self.mock.base + "/sap/opu/odata/sap/API_BUSINESS_PARTNER"}, {"user": M.SAP_USER, "password": "wrong-password-123"})
        with self.assertRaises(ConnectorError) as cm:
            bad.test()
        msg = str(cm.exception)
        self.assertIn("401", msg)
        import base64

        for leaked in ("wrong-password-123", base64.b64encode(f"{M.SAP_USER}:wrong-password-123".encode()).decode(), f"{M.SAP_USER}:wrong-password-123"):
            self.assertNotIn(leaked, msg)  # the mock echoes the Authorization header back in its 401 body

    def test_entity_set_name_is_validated(self):
        with self.assertRaises(ConnectorError):
            list(self.sap().fetch({"entity_set": "A_BusinessPartner?$expand=x"}))


class SalesforceTests(EnterpriseBase):
    def sf(self, **secrets):
        s = secrets or {"client_id": "cid", "client_secret": "csec", "username": M.SF_USER, "password": M.SF_PASS, "security_token": M.SF_TOKEN_SUFFIX}
        return SalesforceAdapter({"instance_url": self.mock.base, "login_url": self.mock.base}, s)

    def test_oauth_password_flow_then_soql_with_paging_and_incremental(self):
        a = self.sf()
        self.assertTrue(a.test()["ok"])
        rows = list(a.fetch({"sobject": "Account", "columns": ["Id", "Name", "Email__c"], "updated_at_field": "SystemModstamp"}))
        self.assertEqual([r["Id"] for r in rows], ["001A", "001B", "001C"])  # nextRecordsUrl followed
        self.assertNotIn("attributes", rows[0])
        self.assertEqual(self.mock.srv.last_soql, "SELECT Id, Name, Email__c FROM Account ORDER BY SystemModstamp ASC")
        inc = list(self.sf().fetch({"sobject": "Account", "columns": ["Id"], "updated_at_field": "SystemModstamp"}, since=SINCE))
        self.assertEqual([r["Id"] for r in inc], ["001B", "001C"])
        self.assertIn("WHERE SystemModstamp > 2026-02-01T00:00:00Z", self.mock.srv.last_soql)

    def test_direct_access_token_and_discovery(self):
        a = self.sf(access_token=M.SF_ACCESS)
        self.assertEqual([o["name"] for o in a.discover()], ["Account", "Contact"])  # non-queryable objects are not offered
        self.assertFalse(any(c["path"] == "/services/oauth2/token" for c in self.calls()))

    def test_bad_login_does_not_echo_the_password_the_server_echoed(self):
        with self.assertRaises(ConnectorError) as cm:
            self.sf(client_id="c", client_secret="s", username=M.SF_USER, password="nope-nope-nope", security_token="TT").test()
        self.assertNotIn("nope-nope-nopeTT", str(cm.exception))
        self.assertNotIn("nope-nope-nope", str(cm.exception))

    def test_soql_identifiers_cannot_be_injected(self):
        for spec in ({"sobject": "Account WHERE 1=1", "columns": ["Id"]}, {"sobject": "Account", "columns": ["Id; DELETE"]},
                     {"sobject": "Account", "columns": ["Id"], "updated_at_field": "x y"}):
            with self.assertRaises(ConnectorError):
                list(self.sf(access_token=M.SF_ACCESS).fetch(spec))


class OdooTests(EnterpriseBase):
    def odoo(self, pw=M.ODOO_PASS):
        return OdooAdapter({"url": self.mock.base, "db": M.ODOO_DB}, {"user": M.ODOO_USER, "password": pw})

    def test_login_fetch_incremental_and_false_means_empty(self):
        a = self.odoo()
        self.assertIn("17.0", a.test()["detail"])
        rows = list(a.fetch({"model": "res.partner", "columns": ["name", "email", "phone", "write_date"]}))
        self.assertEqual(len(rows), 3)
        self.assertIsNone(next(r for r in rows if r["id"] == 56)["phone"])  # Odoo "False" -> null (phone is a char field)
        inc = list(self.odoo().fetch({"model": "res.partner", "columns": ["name", "write_date"]}, since=SINCE))
        self.assertEqual([r["id"] for r in inc], [56, 57])

    def test_wrong_password_and_model_errors(self):
        with self.assertRaises(ConnectorError) as cm:
            self.odoo("wrong-odoo-password").test()
        self.assertNotIn("wrong-odoo-password", str(cm.exception))
        with self.assertRaises(ConnectorError):
            list(self.odoo().fetch({"model": "no.such.model"}))

    def test_discovery(self):
        self.assertEqual(self.odoo().discover()[0]["name"], "res.partner")


class RestTests(EnterpriseBase):
    def rest(self, token=M.REST_TOKEN):
        return RestAdapter({"base_url": self.mock.base}, {"bearer": token})

    def test_page_pagination_list_path_and_incremental(self):
        spec = {"path": "/api/page", "list_path": "data.items", "pagination": {"type": "page", "param": "page", "size_param": "per_page", "size": 3},
                "since_param": "updated_since"}
        self.assertEqual(len(list(self.rest().fetch(spec))), 7)  # 3 + 3 + 1
        got = list(self.rest().fetch(spec, since=datetime(2026, 1, 5, tzinfo=timezone.utc)))
        self.assertEqual([r["id"] for r in got], [6, 7])  # the mock filters strictly newer than the watermark
        self.assertEqual(self.calls("/api/page")[-1]["query"]["updated_since"][0], "2026-01-05T00:00:00Z")

    def test_cursor_pagination(self):
        rows = list(self.rest().fetch({"path": "/api/cursor", "list_path": "results", "pagination": {"type": "cursor", "next_path": "links.next"}}))
        self.assertEqual(len(rows), 7)

    def test_errors_do_not_leak_the_token(self):
        with self.assertRaises(ConnectorError) as cm:
            list(self.rest("wrong-token-xyz").fetch({"path": "/api/page", "list_path": "data.items"}))
        self.assertNotIn("wrong-token-xyz", str(cm.exception))
        with self.assertRaises(ConnectorError):
            list(self.rest().fetch({"path": "/api/notjson"}))


class ConnectorSecurityTests(EnterpriseBase):
    def test_disabled_unless_hosts_are_allow_listed(self):
        with override_settings(PRIME_ONTOLOGY_CONNECTOR_HOSTS=[], PRIME_ONTOLOGY_ALLOWED_URL_HOSTS=[]):
            with self.assertRaisesRegex(ConnectorError, "disabled"):
                RestAdapter({"base_url": self.mock.base}, {}).test()

    def test_other_hosts_schemes_credentials_and_redirects_are_refused(self):
        with self.assertRaisesRegex(ConnectorError, "allow-list"):
            RestAdapter({"base_url": "http://169.254.169.254"}, {}).test()
        with self.assertRaisesRegex(ConnectorError, "Credentials in the URL"):
            RestAdapter({"base_url": f"http://u:p@127.0.0.1:{self.mock.srv.server_port}"}, {}).test()
        with self.assertRaisesRegex(ConnectorError, "redirect"):
            RestAdapter({"base_url": self.mock.base}, {}).test_path_probe() if False else list(RestAdapter({"base_url": self.mock.base}, {}).fetch({"path": "/redirect"}))
        with override_settings(PRIME_ONTOLOGY_ALLOW_HTTP_URLS=False):
            with self.assertRaisesRegex(ConnectorError, "https"):
                RestAdapter({"base_url": self.mock.base}, {}).test()

    def test_registry_and_unknown_kind(self):
        self.assertEqual(set(ADAPTERS), {"mysql", "rest", "odoo", "salesforce", "sap_odata", "file", "datalake", "push"})
        o = make_ontology()
        src = FabricSource.objects.create(ontology=o, name="x", kind="nope", mapping={})
        with self.assertRaises(ConnectorError):
            build_adapter(src)


class OneCustomerAcrossSapSalesforceOdooTests(EnterpriseBase):
    """The document's headline example: the same customer in three systems becomes ONE semantic entity."""

    def setUp(self):
        super().setUp()
        self.o = make_ontology()
        ident = [{"keys": ["email"], "normalize": "email"}]
        self.sap = FabricSource.objects.create(
            ontology=self.o, name="SAP", kind="sap_odata", priority=90, config={"base_url": self.mock.base + "/sap/opu/odata/sap/API_BUSINESS_PARTNER"},
            secret_enc=crypto.encrypt_json({"user": M.SAP_USER, "password": M.SAP_PASS}),
            mapping={"entities": [{"class": "Customer", "entity_set": "A_BusinessPartner", "id": "BusinessPartner", "updated_at_field": "LastChangeDate", "identity": ident,
                                   "fields": {"BusinessPartnerFullName": "customerName", "EmailAddress": "email", "City": "city", "BusinessPartner": "customerId"}}]})
        self.sf = FabricSource.objects.create(
            ontology=self.o, name="Salesforce", kind="salesforce", priority=60, config={"instance_url": self.mock.base, "login_url": self.mock.base},
            secret_enc=crypto.encrypt_json({"client_id": "c", "client_secret": "s", "username": M.SF_USER, "password": M.SF_PASS, "security_token": M.SF_TOKEN_SUFFIX}),
            mapping={"entities": [{"class": "Customer", "sobject": "Account", "id": "Id", "columns": ["Id", "Name", "Email__c", "Phone"], "updated_at_field": "SystemModstamp",
                                   "identity": ident, "fields": {"Name": "customerName", "Email__c": "email", "Phone": "phone"}}]})
        self.odoo = FabricSource.objects.create(
            ontology=self.o, name="Odoo", kind="odoo", priority=30, config={"url": self.mock.base, "db": M.ODOO_DB},
            secret_enc=crypto.encrypt_json({"user": M.ODOO_USER, "password": M.ODOO_PASS}),
            mapping={"entities": [{"class": "Customer", "model": "res.partner", "id": "id", "columns": ["name", "email", "phone", "write_date"], "updated_at_field": "write_date",
                                   "identity": ident, "fields": {"name": "customerName", "email": "email", "phone": "phone"}}]})

    def test_three_systems_one_entity_with_full_provenance(self):
        runs = [sync_source(s) for s in (self.sap, self.sf, self.odoo)]
        self.assertEqual([r.status for r in runs], ["succeeded"] * 3, [r.error for r in runs])
        ents = entities(self.o)
        self.assertEqual(len(ents), 7)  # 3 SAP + 3 SF + 3 Odoo records; Ann is in all three -> 9 - 2 = 7 entities
        ann = next(e for e in ents if e.canonical.get("email") == "ann@acme.com")
        self.assertEqual(ann.record_count, 3)
        self.assertEqual(ann.canonical["customerName"], "Ann Lee")  # SAP (priority 90) wins the name
        self.assertEqual(ann.canonical["phone"], "555-0100")  # SAP has no phone; Salesforce (60) beats Odoo (30)
        self.assertEqual(ann.provenance["customerId"]["source"], "SAP")
        self.assertEqual(ann.provenance["phone"]["source"], "Salesforce")
        self.assertEqual({r.source.name for r in ann.records.all()}, {"SAP", "Salesforce", "Odoo"})
        self.assertEqual(ann.canonical["customerId"], "0000100123")  # the SAP customer number

    def test_incremental_sync_only_touches_what_changed_in_each_system(self):
        for s in (self.sap, self.sf, self.odoo):
            sync_source(s)
        self.mock.state.calls.clear()
        self.mock.state.sap_bp[0]["City"] = "Chennai"
        self.mock.state.sap_bp[0]["LastChangeDate"] = "2026-04-01T00:00:00Z"
        r_sap, r_sf, r_odoo = (sync_source(s) for s in (self.sap, self.sf, self.odoo))
        self.assertEqual((r_sap.stats["changed"], r_sap.stats.get("new", 0), r_sap.stats.get("unchanged", 0)), (1, 0, 0))  # only 1 row fetched
        self.assertEqual((r_sf.stats.get("changed", 0), r_sf.stats.get("new", 0)), (0, 0))
        self.assertEqual((r_odoo.stats.get("changed", 0), r_odoo.stats.get("new", 0)), (0, 0))
        self.assertIn("SystemModstamp >", self.mock.srv.last_soql)  # Salesforce was asked only for newer records
        ann = next(e for e in entities(self.o) if e.canonical.get("email") == "ann@acme.com")
        self.assertEqual(ann.canonical["city"], "Chennai")
        self.assertTrue(FabricEvent.objects.filter(kind="record_changed", source=self.sap, payload__changes__0__new="Chennai").exists())

    def test_a_system_going_down_leaves_the_fabric_intact_and_visible_as_failed(self):
        sync_source(self.sap)
        before = FabricRecord.objects.count()
        with override_settings(PRIME_ONTOLOGY_CONNECTOR_HOSTS=["other.example"]):
            r = sync_source(self.sap)
        self.assertEqual(r.status, "failed")
        self.assertIn("allow-list", r.error)
        self.assertEqual(FabricRecord.objects.count(), before)
        self.sap.refresh_from_db()
        self.assertEqual(self.sap.last_status, "failed")
        self.assertIsNotNone(self.sap.last_synced_at)  # freshness still reflects the last GOOD sync
        self.assertNotIn(M.SAP_PASS, self.sap.last_error)
