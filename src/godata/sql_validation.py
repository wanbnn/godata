from __future__ import annotations

from sqlglot import errors, exp, parse


class UnsafeQueryError(ValueError):
    """A consulta não satisfaz a política somente-leitura."""


_MUTATING_NODES = {
    "Alter",
    "Command",
    "Commit",
    "Copy",
    "Create",
    "Delete",
    "Drop",
    "Execute",
    "Grant",
    "Insert",
    "Into",
    "LoadData",
    "Merge",
    "Rollback",
    "Set",
    "Transaction",
    "TruncateTable",
    "Update",
    "Use",
}
_BLOCKED_FUNCTIONS = {"OPENROWSET", "OPENDATASOURCE", "OPENQUERY"}


def validate_read_only_query(sql: str, max_length: int) -> None:
    if len(sql) > max_length:
        raise UnsafeQueryError(f"A consulta excede o limite de {max_length} caracteres")

    try:
        statements = [statement for statement in parse(sql, read="tsql") if statement is not None]
    except errors.ParseError as exc:
        raise UnsafeQueryError("SQL T-SQL inválido") from exc

    if len(statements) != 1:
        raise UnsafeQueryError("Envie exatamente uma instrução SQL")

    statement = statements[0]
    if not isinstance(statement, exp.Query):
        raise UnsafeQueryError("Somente SELECT e CTEs de leitura são permitidos")

    for node in statement.walk():
        if type(node).__name__ in _MUTATING_NODES:
            raise UnsafeQueryError(f"Operação {type(node).__name__.upper()} não permitida")
        if isinstance(node, exp.Anonymous) and node.name.upper() in _BLOCKED_FUNCTIONS:
            raise UnsafeQueryError(f"Função {node.name.upper()} não permitida")
