"""Condition DSL for pipeline `if` expressions.

Grammar, AST, transformer, static type-checking and evaluation for the small
boolean expression language used in `pipeline_manager.pipelines[].if`.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from lark import Lark, Transformer
from lark.exceptions import UnexpectedInput

if TYPE_CHECKING:
    from .model import Job


class ConditionError(Exception):
    """Base class for errors raised while compiling or validating a condition."""


class ConditionSyntaxError(ConditionError):
    """Raised when a condition string fails to parse."""


class ConditionFieldError(ConditionError):
    """Raised when a condition references a field outside `ALLOWED_FIELDS`."""


class ConditionTypeError(ConditionError):
    """Raised when a condition is not statically typed as `bool`."""


# Field paths a condition may reference, and their expected Python type.
# Deliberately hand-maintained (not auto-derived from the Job model): the
# failure mode of a missing entry is a safe compile-time error, not a
# silently over-permissive condition surface.
ALLOWED_FIELDS: dict[tuple[str, ...], type] = {
    ("job_type",): str,
    ("device_id",): str,
}


@dataclass(frozen=True)
class FieldRef:
    """Reference to a `job.xxx` field. `path` excludes the leading "job"."""

    path: tuple[str, ...]


@dataclass(frozen=True)
class Literal:
    """A string / bool / null literal."""

    value: str | bool | None


@dataclass(frozen=True)
class Eq:
    """`left == right`."""

    left: Expr
    right: Expr


@dataclass(frozen=True)
class Ne:
    """`left != right`."""

    left: Expr
    right: Expr


@dataclass(frozen=True)
class And:
    """`left && right`."""

    left: Expr
    right: Expr


@dataclass(frozen=True)
class Or:
    """`left || right`."""

    left: Expr
    right: Expr


@dataclass(frozen=True)
class Not:
    """`!operand`."""

    operand: Expr


Expr = FieldRef | Literal | Eq | Ne | And | Or | Not

_GRAMMAR = r"""
?start: expr

?expr: or_expr

?or_expr: and_expr
        | or_expr "||" and_expr   -> or_

?and_expr: not_expr
         | and_expr "&&" not_expr -> and_

?not_expr: "!" not_expr           -> not_
         | comparison

?comparison: atom "==" atom       -> eq
           | atom "!=" atom       -> ne
           | atom

?atom: FIELD                      -> field
     | STRING                     -> string
     | "true"                     -> true
     | "false"                    -> false
     | "null"                     -> null
     | "(" expr ")"

FIELD: /job(\.[a-zA-Z_][a-zA-Z0-9_]*)+/
STRING: ESCAPED_STRING

%import common.ESCAPED_STRING
%import common.WS
%ignore WS
"""

# Created once at import time (Engine startup), reused for every condition
# compiled afterwards. Never re-created per job.
_PARSER = Lark(_GRAMMAR, parser="lalr")


class _ConditionTransformer(Transformer):
    """Lark parse tree -> `Expr` AST.

    Methods are `@staticmethod` (Lark's `Transformer` dispatches them via
    `getattr(self, rule_name)(children)`, which resolves a staticmethod to
    its plain function, so `self` is never needed here).
    """

    @staticmethod
    def field(items: list) -> FieldRef:
        (tok,) = items
        return FieldRef(path=tuple(str(tok).split(".")[1:]))

    @staticmethod
    def string(items: list) -> Literal:
        (tok,) = items
        # Strip surrounding quotes. Backslash-escape handling is not
        # implemented yet (deferred, see design doc 8章).
        return Literal(value=str(tok)[1:-1])

    @staticmethod
    def true(_items: list) -> Literal:
        return Literal(value=True)

    @staticmethod
    def false(_items: list) -> Literal:
        return Literal(value=False)

    @staticmethod
    def null(_items: list) -> Literal:
        return Literal(value=None)

    @staticmethod
    def eq(items: list) -> Eq:
        left, right = items
        return Eq(left, right)

    @staticmethod
    def ne(items: list) -> Ne:
        left, right = items
        return Ne(left, right)

    @staticmethod
    def and_(items: list) -> And:
        left, right = items
        return And(left, right)

    @staticmethod
    def or_(items: list) -> Or:
        left, right = items
        return Or(left, right)

    @staticmethod
    def not_(items: list) -> Not:
        (operand,) = items
        return Not(operand)


def _validate_fields(node: Expr) -> None:
    """Recursively check that every `FieldRef` is in `ALLOWED_FIELDS`.

    Raises:
        ConditionFieldError: If an unknown field is referenced.

    """
    match node:
        case FieldRef(path):
            if path not in ALLOWED_FIELDS:
                dotted = ".".join(("job", *path))
                message = f"unknown field in condition: {dotted!r}"
                raise ConditionFieldError(message)
        case Eq(left, right) | Ne(left, right) | And(left, right) | Or(left, right):
            _validate_fields(left)
            _validate_fields(right)
        case Not(operand):
            _validate_fields(operand)
        case Literal(_):
            pass


def static_type(node: Expr) -> type:
    """Compute the static type of `node`.

    Returns:
        `bool` for comparisons/`&&`/`||`/`!`, or the literal/field type.

    Raises:
        ConditionTypeError: If the expression is not well-typed.
        TypeError: If `node` is not a recognized `Expr` variant (unreachable
            in practice; guards against future `Expr` additions).

    """
    match node:
        case Literal(value):
            return type(value) if value is not None else type(None)
        case FieldRef(path):
            return ALLOWED_FIELDS[path]
        case Eq(left, right) | Ne(left, right):
            left_type, right_type = static_type(left), static_type(right)
            if left_type is not right_type:
                message = f"cannot compare {left_type} and {right_type}"
                raise ConditionTypeError(message)
            return bool
        case And(left, right) | Or(left, right):
            for operand in (left, right):
                operand_type = static_type(operand)
                if operand_type is not bool:
                    message = f"&&/|| operand must be bool, got {operand_type}"
                    raise ConditionTypeError(message)
            return bool
        case Not(operand):
            operand_type = static_type(operand)
            if operand_type is not bool:
                message = f"! operand must be bool, got {operand_type}"
                raise ConditionTypeError(message)
            return bool
    message = f"unhandled expression node: {node!r}"  # pragma: no cover
    raise TypeError(message)  # pragma: no cover


def compile_condition(source: str) -> Expr:
    """Parse, field-validate and type-check a condition string.

    Note: also propagates `ConditionFieldError` from `_validate_fields()`.

    Args:
        source: The `if:` expression, e.g. `job.job_type == "sampling"`.

    Returns:
        The compiled AST, ready to be evaluated with `evaluate()`.

    Raises:
        ConditionSyntaxError: If `source` is not valid condition syntax.
        ConditionTypeError: If `source` is not statically typed as `bool`.

    """
    try:
        tree = _PARSER.parse(source)
    except UnexpectedInput as exc:
        message = f"invalid condition syntax: {source!r}"
        raise ConditionSyntaxError(message) from exc

    ast = _ConditionTransformer().transform(tree)
    _validate_fields(ast)

    root_type = static_type(ast)
    if root_type is not bool:
        message = f"condition must evaluate to bool, got {root_type}: {source!r}"
        raise ConditionTypeError(message)

    return ast


def evaluate(node: Expr, job: Job) -> Any:  # noqa: ANN401, PLR0911
    """Evaluate `node` against `job`.

    Assumes `node` was produced by `compile_condition()`, so field paths are
    known to exist on `job` and comparisons are known to be type-safe.

    Returns:
        The condition's value: `bool` at the root, though sub-expressions
        may yield `str`/`bool`/`None` (field/literal values).

    Raises:
        TypeError: If `node` is not a recognized `Expr` variant (unreachable
            in practice; guards against future `Expr` additions).

    """
    match node:
        case Literal(value):
            return value
        case FieldRef(path):
            return functools.reduce(getattr, path, job)
        case Eq(left, right):
            return evaluate(left, job) == evaluate(right, job)
        case Ne(left, right):
            return evaluate(left, job) != evaluate(right, job)
        case And(left, right):
            return evaluate(left, job) and evaluate(right, job)
        case Or(left, right):
            return evaluate(left, job) or evaluate(right, job)
        case Not(operand):
            return not evaluate(operand, job)
    message = f"unhandled expression node: {node!r}"  # pragma: no cover
    raise TypeError(message)  # pragma: no cover
