import pytest

from godata.sql_validation import UnsafeQueryError, validate_read_only_query


@pytest.mark.parametrize(
    "query",
    [
        "SELECT id, nome FROM dbo.clientes WHERE id = ?",
        """SELECT
                 cliente_id,
                 nome,
                 criado_em
             FROM dbo.clientes
             WHERE ativo = ?
             ORDER BY nome""",
        "WITH ativos AS (SELECT id FROM dbo.clientes WHERE ativo = 1) SELECT * FROM ativos",
        "SELECT TOP (10) COUNT(*) AS total FROM dbo.pedidos",
    ],
)
def test_accepts_read_queries(query):
    validate_read_only_query(query, 10_000)


@pytest.mark.parametrize(
    "query",
    [
        "DELETE FROM dbo.clientes",
        "UPDATE dbo.clientes SET ativo = 0",
        "INSERT INTO dbo.log (texto) VALUES ('x')",
        "SELECT * INTO dbo.copia FROM dbo.clientes",
        "EXEC dbo.recalcular",
        "SELECT 1; DROP TABLE dbo.clientes",
        "SELECT * FROM OPENROWSET('SQLNCLI', 'x', 'SELECT 1') AS x",
    ],
)
def test_rejects_unsafe_queries(query):
    with pytest.raises(UnsafeQueryError):
        validate_read_only_query(query, 10_000)
