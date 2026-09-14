"""GoData: proxy e SDK Python para Microsoft SQL Server."""

from .client import Engine, GoDataError, GoDataHTTPError, GoDataJobError, QueryJob, QueryResult, create_engine

__all__ = [
    "Engine", "GoDataError", "GoDataHTTPError", "GoDataJobError", "QueryJob", "QueryResult", "create_engine",
]
__version__ = "0.3.0"
