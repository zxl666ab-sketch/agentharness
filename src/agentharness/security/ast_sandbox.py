"""AST and syntax level sandbox for evaluating untrusted Python code and expressions."""

from __future__ import annotations

import ast
from typing import Any


class ASTSecurityError(PermissionError):
    """Raised when dangerous syntax, private attribute reflection, or banned calls are detected."""


# Banned module names and built-in names
BANNED_IMPORTS = frozenset([
    "os", "sys", "subprocess", "shutil", "socket", "http", "urllib", "requests",
    "ctypes", "importlib", "pickle", "shelve", "posix", "nt", "pty", "commands",
    "signal", "multiprocessing", "threading", "builtins", "__builtin__",
])

BANNED_CALLS = frozenset([
    "eval", "exec", "compile", "__import__", "open", "getattr", "setattr",
    "delattr", "hasattr", "globals", "locals", "vars", "breakpoint", "exit", "quit",
])


class SecurityNodeVisitor(ast.NodeVisitor):
    """Deep AST validator that inspects code for forbidden constructs and reflection tricks."""

    def __init__(self, max_depth: int = 40):
        self.max_depth = max_depth
        self._current_depth = 0

    def generic_visit(self, node: ast.AST) -> None:
        self._current_depth += 1
        if self._current_depth > self.max_depth:
            raise ASTSecurityError(f"AST depth exceeded maximum limit ({self.max_depth})")
        try:
            super().generic_visit(node)
        finally:
            self._current_depth -= 1

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            base_mod = alias.name.split(".")[0]
            if base_mod in BANNED_IMPORTS:
                raise ASTSecurityError(f"Import of banned module '{alias.name}' is prohibited")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            base_mod = node.module.split(".")[0]
            if base_mod in BANNED_IMPORTS:
                raise ASTSecurityError(f"Import from banned module '{node.module}' is prohibited")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # Block dunder and private attribute reflection (e.g. __class__, __subclasses__, __globals__, __dict__)
        if node.attr.startswith("__") or node.attr.startswith("_"):
            raise ASTSecurityError(f"Access to private/dunder attribute '{node.attr}' is prohibited in sandbox")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in BANNED_CALLS:
            raise ASTSecurityError(f"Direct call to forbidden built-in '{node.func.id}()' is prohibited")
        self.generic_visit(node)


def validate_python_code(code_str: str, max_depth: int = 40) -> None:
    """Parse and validate Python source code against security AST constraints."""
    try:
        tree = ast.parse(code_str)
    except SyntaxError as e:
        raise ASTSecurityError(f"Invalid Python syntax: {e}") from e

    visitor = SecurityNodeVisitor(max_depth=max_depth)
    visitor.visit(tree)
