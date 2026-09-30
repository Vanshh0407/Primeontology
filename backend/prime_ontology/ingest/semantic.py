"""Document sections -> ontology candidate with provenance (page/section/snippet).

Deterministic, explainable extraction (lexicon + defined-terms + repeated
capitalised phrases). An optional LLM pass (`llm.py`) can refine candidates
but never bypasses human review.
"""
import re
from collections import Counter, defaultdict

from ..naming import to_label

# concept -> (trigger regex, parent, comment)
LEXICON = {
    "Contract": (r"\b(this\s+)?(agreement|contract)\b", None, "The legal agreement itself."),
    "Party": (r"\b(part(y|ies)|between\s+[A-Z])", None, "A signatory to the contract."),
    "Customer": (r"\b(customer|client|buyer|purchaser)\b", "Party", "Party that receives goods or services."),
    "Supplier": (r"\b(supplier|vendor|seller|provider|contractor)\b", "Party", "Party that supplies goods or services."),
    "Clause": (r"\b(clause|section|article|schedule|exhibit)\s+\d", None, "A numbered provision of the contract."),
    "Obligation": (r"\b(shall|must|agrees?\s+to|is\s+required\s+to|obligat\w+|undertakes?)\b", None, "A duty a party must perform."),
    "Payment": (r"\b(payment|invoice|fees?|price|pay(able)?|remuneration|compensation)\b", None, "A monetary payment term."),
    "EffectiveDate": (r"\beffective\s+(date|as\s+of)\b", None, "Date the contract takes effect."),
    "ExpirationDate": (r"\b(expir\w+|end\s+date|term\s+of\s+this|shall\s+terminate\s+on)\b", None, "Date the contract ends."),
    "Term": (r"\b(initial\s+term|renewal\s+term|term\s+of)\b", None, "Duration of the agreement."),
    "Jurisdiction": (r"\b(jurisdiction|courts?\s+of|venue)\b", None, "Legal jurisdiction for disputes."),
    "GoverningLaw": (r"\b(governed\s+by|governing\s+law|laws?\s+of\s+the)\b", None, "Law governing the contract."),
    "Termination": (r"\b(terminat\w+|cancell?ation)\b", None, "Conditions for ending the contract."),
    "Confidentiality": (r"\b(confidential\w*|non-disclosure|NDA)\b", None, "Confidential information provisions."),
    "Liability": (r"\b(liab\w+|indemn\w+|damages)\b", None, "Liability and indemnification."),
    "Warranty": (r"\b(warrant(y|ies|s)|represents?\s+and\s+warrants?)\b", None, "Warranties given by a party."),
    "Deliverable": (r"\b(deliverables?|delivery|deliver(s|ed)?)\b", None, "Goods or work product to be delivered."),
    "Service": (r"\b(services?|service\s+level|SLA)\b", None, "Services provided under the contract."),
    "DisputeResolution": (r"\b(arbitrat\w+|dispute\s+resolution|mediation)\b", None, "How disputes are resolved."),
    "IntellectualProperty": (r"\b(intellectual\s+property|copyright|patent|trademark)\b", None, "IP ownership and licensing."),
    "Penalty": (r"\b(penalt(y|ies)|liquidated\s+damages|late\s+fee)\b", None, "Penalties for breach or delay."),
    "Regulation": (r"\b(regulat\w+|compliance|statute|applicable\s+law)\b", None, "Regulatory obligations."),
}
MIN_HITS = {"Party": 1, "Clause": 1}

# (domain, property, range, requires both present)
RELATIONS = [
    ("Contract", "hasParty", "Party"), ("Contract", "hasCustomer", "Customer"), ("Contract", "hasSupplier", "Supplier"),
    ("Contract", "contains", "Clause"), ("Contract", "creates", "Obligation"), ("Contract", "requires", "Payment"),
    ("Contract", "hasTerm", "Term"), ("Contract", "governedBy", "GoverningLaw"), ("Contract", "hasJurisdiction", "Jurisdiction"),
    ("Contract", "hasTermination", "Termination"), ("Contract", "hasWarranty", "Warranty"),
    ("Contract", "hasLiability", "Liability"), ("Contract", "hasConfidentiality", "Confidentiality"),
    ("Contract", "hasDeliverable", "Deliverable"), ("Contract", "providesService", "Service"),
    ("Contract", "hasDisputeResolution", "DisputeResolution"), ("Contract", "hasPenalty", "Penalty"),
    ("Contract", "subjectToRegulation", "Regulation"), ("Obligation", "boundTo", "Party"),
    ("Payment", "payableBy", "Customer"), ("Payment", "payableTo", "Supplier"),
    ("Clause", "definesObligation", "Obligation"),
]

DATA_PROPS = [  # (class, name, datatype, regex capturing an example value)
    ("Contract", "effectiveDate", "date", r"effective\s+(?:date|as\s+of)[^\n\d]{0,30}([A-Z][a-z]+\s+\d{1,2},?\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})"),
    ("Contract", "expirationDate", "date", r"(?:expir\w+|end\s+date)[^\n\d]{0,30}([A-Z][a-z]+\s+\d{1,2},?\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})"),
    ("Contract", "governingLaw", "string", r"laws?\s+of\s+(?:the\s+)?((?:State|Republic|Kingdom|Province)\s+of\s+[A-Z][\w ]+|[A-Z][a-z]+(?:\s[A-Z][a-z]+)?)"),
    ("Payment", "amount", "decimal", r"((?:USD|EUR|INR|GBP|\$|€|₹|£)\s?\d[\d,]*(?:\.\d+)?)"),
    ("Payment", "paymentDueDays", "integer", r"within\s+(\w+)\s*(?:\(\d+\)\s*)?days"),
]

STOP = {"The", "This", "That", "These", "Each", "Any", "All", "Such", "Either", "Neither", "In", "If", "For", "On", "By",
        "To", "And", "Or", "Of", "A", "An", "As", "At", "No", "Not", "It", "Its", "Section", "Article", "Clause", "Page"}


def _snippet(text: str, m: re.Match, width=110) -> str:
    a, b = max(0, m.start() - width // 2), min(len(text), m.end() + width // 2)
    return re.sub(r"\s+", " ", text[a:b]).strip()


def extract_candidate(sections: list[dict], document: str, max_evidence=5) -> dict:
    evidence = defaultdict(list)
    hits = Counter()
    defined_terms = Counter()
    phrase_counts = Counter()
    phrase_ev = defaultdict(list)
    data_examples = {}

    for s in sections:
        text = s["text"]
        loc = {"document": document, "page": s.get("page"), "section": s.get("heading")}
        haystack = f'{s.get("heading") or ""}\n{text}'
        for concept, (pat, _, _) in LEXICON.items():
            for m in re.finditer(pat, haystack, re.I if concept not in ("Party",) else 0):
                hits[concept] += 1
                if len(evidence[concept]) < max_evidence and not any(e["section"] == loc["section"] and e["page"] == loc["page"] for e in evidence[concept]):
                    evidence[concept].append({**loc, "snippet": _snippet(haystack, m)})
        for m in re.finditer(r'[("“]\s*(?:the\s+)?[“"]?([A-Z][A-Za-z]+(?: [A-Z][A-Za-z]+)?)[”"]\s*[)”]', text):
            defined_terms[m.group(1)] += 1
            phrase_ev[m.group(1)].append({**loc, "snippet": _snippet(text, m)})
        for m in re.finditer(r"\b([A-Z][a-z]+(?: [A-Z][a-z]+){1,3})\b", text):
            ph = m.group(1)
            if ph.split()[0] in STOP:
                continue
            phrase_counts[ph] += 1
            if len(phrase_ev[ph]) < 3:
                phrase_ev[ph].append({**loc, "snippet": _snippet(text, m)})
        for cls, prop, _, pat in DATA_PROPS:
            if (cls, prop) not in data_examples:
                m = re.search(pat, haystack, re.I)
                if m:
                    data_examples[(cls, prop)] = {"value": m.group(1).strip(), **loc, "snippet": _snippet(haystack, m)}

    classes = {}
    total = max(1, sum(len(s["text"]) for s in sections))
    for concept, (_, parent, comment) in LEXICON.items():
        if hits[concept] >= MIN_HITS.get(concept, 1):
            conf = min(0.95, 0.45 + 0.1 * min(hits[concept], 5))
            classes[concept] = {"name": concept, "label": to_label(concept), "comment": comment, "parents": [],
                                "confidence": round(conf, 2), "occurrences": hits[concept], "evidence": evidence[concept],
                                "source": {"document": document}}
    for concept, (_, parent, _) in LEXICON.items():
        if concept in classes and parent and parent in classes:
            classes[concept]["parents"] = [parent]

    # defined terms (e.g. ("Buyer")) -> role classes under Party
    for term, n in defined_terms.items():
        name = re.sub(r"\W", "", term)
        if name and name not in classes and term not in STOP and len(name) > 2:
            parent = "Party" if "Party" in classes else None
            classes[name] = {"name": name, "label": to_label(name), "comment": "Defined term in the document.",
                             "parents": [parent] if parent else [], "confidence": 0.7, "occurrences": n,
                             "evidence": phrase_ev[term][:max_evidence], "source": {"document": document}}
    # recurring capitalised phrases
    for ph, n in phrase_counts.most_common(15):
        name = re.sub(r"\W", "", ph.title())
        if n >= 3 and name not in classes and len(name) > 4 and len(classes) < 45:
            classes[name] = {"name": name, "label": ph, "comment": "Recurring term discovered in the document.",
                             "parents": [], "confidence": round(min(0.6, 0.25 + 0.05 * n), 2), "occurrences": n,
                             "evidence": phrase_ev[ph][:3], "source": {"document": document}}

    oprops = []
    for dom, name, rng in RELATIONS:
        if dom in classes and rng in classes:
            oprops.append({"name": name, "label": to_label(name), "domain": dom, "range": rng,
                           "cardinality": "many-to-many", "required": False,
                           "confidence": round(min(classes[dom]["confidence"], classes[rng]["confidence"]) * 0.9, 2),
                           "evidence": classes[rng]["evidence"][:2], "source": {"document": document}})
    dprops = []
    for cls, name, dt, _ in DATA_PROPS:
        if cls in classes and (cls, name) in data_examples:
            ex = data_examples[(cls, name)]
            dprops.append({"name": name, "label": to_label(name), "domain": cls, "datatype": dt, "required": False,
                           "identifier": False, "confidence": 0.8, "exampleValue": ex["value"],
                           "evidence": [{k: ex[k] for k in ("document", "page", "section", "snippet")}],
                           "source": {"document": document}})
    return {"classes": list(classes.values()), "dataProperties": dprops, "objectProperties": oprops}
