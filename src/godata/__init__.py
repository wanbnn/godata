"""GoData: proxy e SDK Python para Microsoft SQL Server."""

from .client import Engine, GoDataError, GoDataHTTPError, QueryResult, create_engine

__all__ = ["Engine", "GoDataError", "GoDataHTTPError", "QueryResult", "create_engine"]
__version__ = "0.2.0"
