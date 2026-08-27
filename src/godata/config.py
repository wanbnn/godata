from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Mapping


class ConfigurationError(RuntimeError):
    """Configuração ausente ou inválida."""


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} deve ser um número inteiro") from exc
    if value <= 0:
        raise ConfigurationError(f"{name} deve ser maior que zero")
    return value


def _boolean(name: str, default: bool) -> bool:
    raw = os.getenv(name, str(default)).strip().lower()
    if raw in {"1", "true", "yes", "sim"}:
        return True
    if raw in {"0", "false", "no", "nao", "não"}:
        return False
    raise ConfigurationError(f"{name} deve ser true ou false")


def _targets(raw: str) -> dict[str, frozenset[str]]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigurationError("GODATA_ALLOWED_TARGETS deve ser um objeto JSON válido") from exc
    if not isinstance(parsed, dict) or not parsed:
        raise ConfigurationError("GODATA_ALLOWED_TARGETS deve liberar ao menos um servidor")

    result: dict[str, frozenset[str]] = {}
    for server, databases in parsed.items():
        if not isinstance(server, str) or not server.strip():
            raise ConfigurationError("Todo servidor permitido deve ter um nome")
        if not isinstance(databases, list) or not databases:
            raise ConfigurationError(f"O servidor {server!r} deve possuir uma lista de bancos")
        if not all(isinstance(database, str) and database.strip() for database in databases):
            raise ConfigurationError(f"A lista de bancos de {server!r} é inválida")
        result[server.strip().casefold()] = frozenset(database.strip().casefold() for database in databases)
    return result


@dataclass(frozen=True, slots=True)
class Settings:
    api_key: str
    allowed_targets: Mapping[str, frozenset[str]]
    odbc_driver: str = "ODBC Driver 18 for SQL Server"
    encrypt: bool = True
    trust_server_certificate: bool = False
    connection_timeout_seconds: int = 10
    query_timeout_seconds: int = 30
    max_rows: int = 10_000
    max_query_length: int = 100_000
    max_concurrent_queries: int = 10

    @classmethod
    def from_env(cls) -> "Settings":
        api_key = os.getenv("GODATA_API_KEY", "")
        if len(api_key) < 24:
            raise ConfigurationError("GODATA_API_KEY deve possuir ao menos 24 caracteres")

        driver = os.getenv("GODATA_ODBC_DRIVER", "ODBC Driver 18 for SQL Server").strip()
        if not driver or any(char in driver for char in ";{}\x00"):
            raise ConfigurationError("GODATA_ODBC_DRIVER é inválido")

        return cls(
            api_key=api_key,
            allowed_targets=_targets(os.getenv("GODATA_ALLOWED_TARGETS", "")),
            odbc_driver=driver,
            encrypt=_boolean("GODATA_ENCRYPT", True),
            trust_server_certificate=_boolean("GODATA_TRUST_SERVER_CERTIFICATE", False),
            connection_timeout_seconds=_positive_int("GODATA_CONNECTION_TIMEOUT_SECONDS", 10),
            query_timeout_seconds=_positive_int("GODATA_QUERY_TIMEOUT_SECONDS", 30),
            max_rows=_positive_int("GODATA_MAX_ROWS", 10_000),
            max_query_length=_positive_int("GODATA_MAX_QUERY_LENGTH", 100_000),
            max_concurrent_queries=_positive_int("GODATA_MAX_CONCURRENT_QUERIES", 10),
        )

    def target_is_allowed(self, server: str, database: str) -> bool:
        databases = self.allowed_targets.get(server.strip().casefold())
        return databases is not None and ("*" in databases or database.strip().casefold() in databases)
