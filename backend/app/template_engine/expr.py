"""Безопасный вычислитель выражений зон риска из шаблона: «p*i >= 10 or (p >= 4 and i == 3)».

Разрешены только переменные p (вероятность) и i (влияние), целые числа, + - * /, сравнения,
and/or/not и скобки. Никакого eval: выражение разбирается через ast с белым списком узлов.
"""

import ast
import operator
from functools import lru_cache

VARIABLES = ("p", "i")
OTHERWISE = "otherwise"

_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
_CMP = {
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
}


_ALLOWED = (
    ast.Expression,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.UnaryOp,
    ast.Not,
    ast.USub,
    ast.BinOp,
    ast.Compare,
    ast.Load,
    *_BIN,
    *_CMP,
)


class ExpressionError(ValueError):
    pass


@lru_cache(maxsize=256)
def compile_expr(source: str) -> ast.Expression:
    if source.strip() == OTHERWISE:
        return ast.Expression(body=ast.Constant(True))
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"Синтаксическая ошибка в выражении «{source}»") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if node.id not in VARIABLES:
                raise ExpressionError(f"Недопустимая переменная «{node.id}» (разрешены p и i)")
        elif isinstance(node, ast.Constant):
            if not isinstance(node.value, int | float) or isinstance(node.value, bool):
                raise ExpressionError("Допустимы только числовые константы")
        elif not isinstance(node, _ALLOWED):
            raise ExpressionError(f"Недопустимая конструкция в выражении «{source}»: {type(node).__name__}")
    return tree


def evaluate(source: str, p: int, i: int) -> bool:
    return bool(_eval(compile_expr(source).body, {"p": p, "i": i}))


def _eval(node: ast.AST, env: dict[str, int]):
    match node:
        case ast.Constant(value=v):
            return v
        case ast.Name(id=name):
            return env[name]
        case ast.UnaryOp(op=ast.Not(), operand=x):
            return not _eval(x, env)
        case ast.UnaryOp(op=ast.USub(), operand=x):
            return -_eval(x, env)
        case ast.BinOp(left=a, op=op, right=b):
            return _BIN[type(op)](_eval(a, env), _eval(b, env))
        case ast.BoolOp(op=ast.And(), values=vals):
            return all(_eval(v, env) for v in vals)
        case ast.BoolOp(op=ast.Or(), values=vals):
            return any(_eval(v, env) for v in vals)
        case ast.Compare(left=left, ops=ops, comparators=rights):
            current = _eval(left, env)
            for op, right in zip(ops, rights, strict=True):
                value = _eval(right, env)
                if not _CMP[type(op)](current, value):
                    return False
                current = value
            return True
    raise ExpressionError(f"Недопустимый узел {type(node).__name__}")


def zone_for(zones: dict[str, str], p: int, i: int) -> str:
    """Первая сработавшая зона в порядке red → yellow → green (как в шаблоне)."""
    for name in ("red", "yellow", "green"):
        if name in zones and evaluate(zones[name], p, i):
            return name
    return "green"
