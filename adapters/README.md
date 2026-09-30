# Host adapters

The Prime Ontology Workbench is **one module**. Each host application adds only:

| File | Same in all 3 hosts? |
|---|---|
| `app/ontology/page.jsx` (Next.js route) | **Identical, byte for byte** (enforced by a test) |
| `next.config.snippet.js` | Identical |
| `prime-ontology.config.js` | Per host (`context`, header height, optional token) |
| `django/prime_ontology_settings.py` | Per host (role map, tenant attribute) |

Install into a host with `python scripts/integrate.py --host <unicontractai|primesemonto|primeagenticos> --target <host repo>`
(see `docs/INTEGRATION.md`).
