from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


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


def _non_negative_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} deve ser um número inteiro") from exc
    if value < 0:
        raise ConfigurationError(f"{name} deve ser maior ou igual a zero")
    return value


def _boolean(name: str, default: bool) -> bool:
    raw = os.getenv(name, str(default)).strip().lower()
    if raw in {"1", "true", "yes", "sim"}:
        return True
    if raw in {"0", "false", "no", "nao", "não"}:
        return False
    raise ConfigurationError(f"{name} deve ser true ou false")


@dataclass(frozen=True, slots=True)
class Settings:
    api_key: str
    odbc_driver: str = "ODBC Driver 18 for SQL Server"
    encrypt: bool = True
    trust_server_certificate: bool = True
    connection_timeout_seconds: int = 2048
    query_timeout_seconds: int = 0
    max_rows: int = 1_500_000
    max_query_length: int = 100_000
    max_concurrent_queries: int = 10

    @classmethod
    def from_env(cls) -> "Settings":
        # Variáveis definidas no processo têm precedência sobre o .env.
        load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)

        api_key = os.getenv("GODATA_API_KEY", "")
        if len(api_key) < 24:
            raise ConfigurationError("GODATA_API_KEY deve possuir ao menos 24 caracteres")

        driver = os.getenv("GODATA_ODBC_DRIVER", "ODBC Driver 18 for SQL Server").strip()
        if not driver or any(char in driver for char in ";{}\x00"):
            raise ConfigurationError("GODATA_ODBC_DRIVER é inválido")

        return cls(
            api_key=api_key,
            odbc_driver=driver,
            encrypt=_boolean("GODATA_ENCRYPT", True),
            trust_server_certificate=_boolean("GODATA_TRUST_SERVER_CERTIFICATE", True),
            connection_timeout_seconds=_positive_int("GODATA_CONNECTION_TIMEOUT_SECONDS", 2048),
            query_timeout_seconds=_non_negative_int("GODATA_QUERY_TIMEOUT_SECONDS", 0),
            max_rows=_positive_int("GODATA_MAX_ROWS", 1_500_000),
            max_query_length=_positive_int("GODATA_MAX_QUERY_LENGTH", 100_000),
            max_concurrent_queries=_positive_int("GODATA_MAX_CONCURRENT_QUERIES", 10),
        )
