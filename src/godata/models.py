from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    server: str = Field(min_length=1, max_length=255, examples=[r"sqlserver01\PRODUCAO"])
    database: str = Field(min_length=1, max_length=128, examples=["ERP"])
    query: str = Field(min_length=1, examples=["SELECT TOP (10) id, nome FROM dbo.clientes WHERE ativo = ?"])
    parameters: list[Any] = Field(default_factory=list, max_length=1000, examples=[[True]])


class QueryResponse(BaseModel):
    request_id: str
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    elapsed_ms: int


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
