"""Инструмент calc: арифметика без eval — разбор через ast с белым списком узлов."""

import ast
import math
import operator

_BIN = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
MAX_ABS = 1e30


class CalcError(ValueError):
    pass


def evaluate(expression: str) -> float | int:
    if len(expression) > 2000:
        raise CalcError("Слишком длинное выражение")
    try:
        tree = ast.parse(
            expression.replace(",", ".") if _is_decimal_comma(expression) else expression, mode="eval"
        )
    except SyntaxError as exc:
        raise CalcError(f"Синтаксическая ошибка: {exc.msg}") from exc
    value = _num(_eval(tree.body))
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        raise CalcError("Результат не является конечным числом")
    return value


def _num(v):
    """Только вещественные числа в разумных пределах: без комплексных (-8)**0.5, списков в итоге
    и огромных целых — длинная арифметика держит GIL и останавливает весь воркер."""
    if isinstance(v, bool) or not isinstance(v, int | float):
        raise CalcError("Результат должен быть числом")
    if abs(v) > MAX_ABS:
        raise CalcError("Слишком большое число")
    return v


def _round(value, ndigits=None):
    if ndigits is None:
        return round(_num(value))
    if not isinstance(ndigits, int) or isinstance(ndigits, bool) or abs(ndigits) > 20:
        raise CalcError("round: число знаков — целое от -20 до 20")
    return round(_num(value), ndigits)


def _seq(fn):
    def call(*args):
        items = args[0] if len(args) == 1 and isinstance(args[0], list) else list(args)
        if not items:
            raise CalcError("Пустой список аргументов")
        return fn([_num(x) for x in items])

    return call


def _is_decimal_comma(expr: str) -> bool:
    """«12,5 * 3» — десятичная запятая; в вызовах функций запятая — разделитель аргументов."""
    return "(" not in expr and "," in expr


def _eval(node: ast.AST):
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int | float)
        and not isinstance(node.value, bool)
    ):
        return _num(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
        v = _eval(node.operand)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        left, right = _num(_eval(node.left)), _num(_eval(node.right))
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise CalcError("Слишком большая степень")
        try:
            result = _BIN[type(node.op)](left, right)
        except ZeroDivisionError as exc:
            raise CalcError("Деление на ноль") from exc
        except OverflowError as exc:
            raise CalcError("Слишком большое число") from exc
        return _num(result)
    if isinstance(node, ast.List | ast.Tuple):
        return [_eval(e) for e in node.elts]
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _FUNCS
        and not node.keywords
    ):
        args = [_eval(a) for a in node.args]
        try:
            return _num(_FUNCS[node.func.id](*args))
        except TypeError as exc:
            raise CalcError(f"{node.func.id}: неверные аргументы") from exc
    raise CalcError(f"Недопустимый элемент выражения: {type(node).__name__}")


_FUNCS = {
    "round": _round,
    "min": _seq(min),
    "max": _seq(max),
    "sum": _seq(sum),
    "abs": lambda v: abs(_num(v)),
}
