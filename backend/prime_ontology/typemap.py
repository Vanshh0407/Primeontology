"""SQL datatype -> XSD datatype."""
import re

_RULES = [
    (r"^(tinyint\(1\)|bool|boolean|bit)$", "boolean"),
    (r"^(bigint|int8)$", "long"),
    (r"^(smallint|int2|tinyint)$", "short"),
    (r"^(int|integer|int4|mediumint|serial|bigserial|smallserial)$", "integer"),
    (r"^(numeric|decimal|money|number)$", "decimal"),
    (r"^(float|float4|real)$", "float"),
    (r"^(double|double precision|float8)$", "double"),
    (r"^timestamp.*$|^datetime.*$", "dateTime"),
    (r"^date$", "date"),
    (r"^time.*$", "time"),
    (r"^(binary|varbinary|blob|bytea|longblob|mediumblob|tinyblob)$", "base64Binary"),
]


def sql_to_xsd(sql_type: str) -> str:
    t = re.sub(r"\(.*?\)", "", (sql_type or "").strip().lower())
    t = re.sub(r"\s+(unsigned|zerofill|with(out)? time zone)", "", t).strip()
    raw = (sql_type or "").strip().lower()
    for pattern, xsd in _RULES:
        if re.match(pattern, raw) or re.match(pattern, t):
            return xsd
    return "string"
