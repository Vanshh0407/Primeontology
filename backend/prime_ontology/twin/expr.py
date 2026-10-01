"""A small, safe expression language for twin rules (never uses eval).

  creditLimit - sum(Invoice.amount)          arithmetic over the entity's own attributes and aggregates over related entities
  overdueDays > 60 and status != "paid"       comparisons / and / or / not
  max(Invoice.overdueDays) if count(Invoice) > 0 else 0

Missing values propagate as null (unknown); a comparison involving null is unknown and therefore does not fire an alert.
Allowed: + - * / % **(no), comparisons, and/or/not, conditional expression, numbers/strings/booleans, attribute names,
Class.property (aggregated over the 1-hop neighbours of that class) and the functions sum count min max avg abs round coalesce.
"""
import ast
import operator

MAX_NODES = 120
AGG = {"sum", "count", "min", "max", "avg"}
FUNCS = AGG | {"abs", "round", "coalesce"}
_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Mod: operator.mod}
_CMP = {ast.Gt: operator.gt, ast.GtE: operator.ge, ast.Lt: operator.lt, ast.LtE: operator.le, ast.Eq: operator.eq, ast.NotEq: operator.ne}


class ExprError(ValueError):
    pass


def parse(text: str):
    if not isinstance(text, str) or not text.strip():
        raise ExprError("Expression is empty.")
    if len(text) > 600:
        raise ExprError("Expression is longer than 600 characters.")
    try:
        tree = ast.parse(text.strip(), mode="eval")
    except SyntaxError as e:
        raise ExprError(f"Syntax error: {e.msg}") from None
    n = 0
    for node in ast.walk(tree):
        n += 1
        if n > MAX_NODES:
            raise ExprError("Expression is too complex.")
        ok = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp, ast.Constant, ast.Name, ast.Attribute, ast.Call, ast.Load,
              ast.And, ast.Or, ast.Not, ast.USub, ast.UAdd, *_BIN, *_CMP)
        if not isinstance(node, ok):
            raise ExprError(f"'{type(node).__name__}' is not allowed in expressions.")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCS or node.keywords:
                raise ExprError(f"Unknown function. Allowed: {', '.join(sorted(FUNCS))}.")
            if node.func.id in AGG and (len(node.args) != 1 or not isinstance(node.args[0], (ast.Attribute, ast.Name))):
                raise ExprError(f"{node.func.id}() takes one argument: Class.property" + (" or Class" if node.func.id == "count" else "") + ".")
        if isinstance(node, ast.Attribute) and not isinstance(node.value, ast.Name):
            raise ExprError("Only Class.property references are allowed.")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float, str, bool, type(None))):
            raise ExprError("Unsupported literal.")
    return tree


def names_used(tree) -> tuple[set, set]:
    """(own attribute names, (Class, property) references) - used to validate a rule against the ontology."""
    own, refs = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            refs.add((node.value.id, node.attr))
        elif isinstance(node, ast.Name):
            own.add(node.id)
    attr_bases = {c for c, _ in refs}
    func_names = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call)}
    cls_in_agg = {a.id for n in ast.walk(tree) if isinstance(n, ast.Call) for a in n.args if isinstance(a, ast.Name)}
    return own - attr_bases - func_names - cls_in_agg - {"True", "False", "None"}, refs | {(c, None) for c in cls_in_agg}


def evaluate(tree, values: dict, related) -> object:
    """values: this entity's attributes. related(class_name) -> list of attribute dicts of 1-hop neighbours of that class."""

    def num(x):
        return isinstance(x, (int, float)) and not isinstance(x, bool)

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, ast.Name):
            if n.id in ("True", "False", "None"):
                return {"True": True, "False": False, "None": None}[n.id]
            return values.get(n.id)
        if isinstance(n, ast.UnaryOp):
            v = ev(n.operand)
            if isinstance(n.op, ast.Not):
                return None if v is None else (not v)
            return None if not num(v) else (-v if isinstance(n.op, ast.USub) else v)
        if isinstance(n, ast.BinOp):
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Add) and isinstance(a, str) and isinstance(b, str):
                return a + b
            if not (num(a) and num(b)):
                return None
            try:
                return _BIN[type(n.op)](a, b)
            except ZeroDivisionError:
                return None
        if isinstance(n, ast.BoolOp):
            vals = [ev(x) for x in n.values]
            if isinstance(n.op, ast.And):
                if any(v is not None and not v for v in vals):
                    return False
                return None if any(v is None for v in vals) else True
            if any(v is not None and v for v in vals):
                return True
            return None if any(v is None for v in vals) else False
        if isinstance(n, ast.Compare):
            left = ev(n.left)
            for op, comp in zip(n.ops, n.comparators):
                right = ev(comp)
                if left is None or right is None:
                    return None
                try:
                    if isinstance(left, str) != isinstance(right, str) and not isinstance(op, (ast.Eq, ast.NotEq)):
                        return None
                    if isinstance(left, str) and isinstance(right, str) and isinstance(op, (ast.Eq, ast.NotEq)):
                        ok = _CMP[type(op)](left.lower(), right.lower())
                    else:
                        ok = _CMP[type(op)](left, right)
                except TypeError:
                    return None
                if not ok:
                    return False
                left = right
            return True
        if isinstance(n, ast.IfExp):
            c = ev(n.test)
            return None if c is None else ev(n.body if c else n.orelse)
        if isinstance(n, ast.Attribute):
            raise ExprError(f"{n.value.id}.{n.attr} can only be used inside sum/count/min/max/avg.")
        if isinstance(n, ast.Call):
            f = n.func.id
            if f in AGG:
                arg = n.args[0]
                if isinstance(arg, ast.Name):
                    rows = related(arg.id)
                    return len(rows) if f == "count" else None
                rows = related(arg.value.id)
                xs = [r.get(arg.attr) for r in rows]
                xs = [float(x) if num(x) or (isinstance(x, str) and _isnum(x)) else None for x in xs]
                xs = [x for x in xs if x is not None]
                if f == "count":
                    return len(xs)
                if not xs:
                    return 0 if f == "sum" else None
                return {"sum": sum, "min": min, "max": max, "avg": lambda v: sum(v) / len(v)}[f](xs)
            args = [ev(a) for a in n.args]
            if f == "coalesce":
                return next((a for a in args if a is not None), None)
            if f == "abs":
                return abs(args[0]) if len(args) == 1 and num(args[0]) else None
            if f == "round":
                return round(args[0], int(args[1]) if len(args) > 1 and num(args[1]) else 0) if args and num(args[0]) else None
        raise ExprError("Unsupported expression.")

    return ev(tree)


def _isnum(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False
