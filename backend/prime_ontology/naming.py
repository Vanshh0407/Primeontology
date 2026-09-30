"""Semantic naming: database identifiers -> ontology names."""
import re

_WORD_SPLIT = re.compile(r"[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])")


def words(identifier: str) -> list[str]:
    return [w.lower() for w in _WORD_SPLIT.split(identifier) if w]


_IRREGULAR = {"people": "person", "children": "child", "men": "man", "women": "woman", "indices": "index",
              "analyses": "analysis", "statuses": "status", "addresses": "address", "classes": "class"}


def singular(word: str) -> str:
    if word in _IRREGULAR:
        return _IRREGULAR[word]
    if len(word) > 3 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith(("sses", "xes", "ches", "shes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def to_class_name(table: str) -> str:
    """SALES_ORDER / sales_orders -> SalesOrder"""
    ws = words(table)
    if ws:
        ws[-1] = singular(ws[-1])
    return "".join(w.capitalize() for w in ws) or "Thing"


def to_property_name(column: str) -> str:
    """CUSTOMER_ID -> customerId"""
    ws = words(column)
    if not ws:
        return "property"
    return ws[0] + "".join(w.capitalize() for w in ws[1:])


def to_label(name: str) -> str:
    """SalesOrder -> Sales Order"""
    return " ".join(w.capitalize() for w in words(name))


def relationship_name(fk_columns: list[str], target_class: str) -> str:
    """FK column customer_id -> target Customer => hasCustomer;
    FK column billing_customer_id -> hasBillingCustomer (role preserved)."""
    if len(fk_columns) == 1:
        stem = re.sub(r"(_id|_key|_fk|_code)$", "", fk_columns[0], flags=re.I)
        stem_cls = to_class_name(stem) if stem else ""
        if stem_cls and stem_cls != "Thing":
            return "has" + stem_cls
    return "has" + target_class


def inverse_name(rel_name: str, source_class: str) -> str:
    """hasCustomer on SalesOrder -> customerOf... keep simple & readable."""
    return "is" + rel_name[3:] + "Of" if rel_name.startswith("has") else rel_name + "Inverse"
