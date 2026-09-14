from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    server: str = Field(min_length=1, max_length=255, examples=[r"sqlserver01\PRODUCAO"])
    database: str = Field(min_length=1, max_length=128, examples=["ERP"])
    query: str = Field(min_length=1, examples=["UPDATE dbo.clientes SET ativo = ? WHERE id = ?"])
    parameters: list[Any] = Field(default_factory=list, examples=[[True]])


class QueryResponse(BaseModel):
    request_id: str
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    rows_affected: int | None
    truncated: bool
    elapsed_ms: int


class QueryJobResponse(BaseModel):
    query_id: str
    request_id: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    created_at: float
    started_at: float | None
    finished_at: float | None
    elapsed_ms: int
    error: str | None
    version: int


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


class DatabaseInfo(BaseModel):
    name: str


class SchemaInfo(BaseModel):
    name: str


class TableInfo(BaseModel):
    schema_name: str
    name: str
    type: str


class ColumnInfo(BaseModel):
    schema_name: str
    table_name: str
    name: str
    ordinal: int
    data_type: str
    max_length: int
    precision: int
    scale: int
    nullable: bool
