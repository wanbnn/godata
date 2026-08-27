# GoData

O GoData é um proxy HTTP **somente-leitura** para Microsoft SQL Server. Um cliente envia o
servidor, o banco, a consulta T-SQL e os parâmetros; o GoData abre a conexão usando a conta
Windows/Active Directory do próprio processo e devolve as linhas em JSON.

```text
cliente HTTP -> GoData (X-API-Key) -> SQL Server
                                  -> Trusted_Connection=Yes
                                  -> identidade da conta que executa o GoData
```

O cliente nunca envia nem recebe a credencial do domínio. O GoData também não armazena senha
do SQL Server.

## Requisitos

- Windows Server ou Windows 10/11 ingressado no domínio;
- Python 3.11 ou superior;
- Microsoft ODBC Driver 18 for SQL Server;
- uma conta AD dedicada, com permissão **somente SELECT** nos bancos necessários.

> A validação de SQL da aplicação é uma camada adicional. A proteção principal deve ser a
> permissão mínima da conta AD no SQL Server: não conceda `db_owner`, `db_datawriter`, DDL,
> execução de procedures ou acesso administrativo.

## Instalação com PRPM

Com o `prpm` disponível, dentro desta pasta:

```powershell
prpm install
Copy-Item .env.example .env
```

O `prpm install` cria a `.venv`, resolve dependências normais e de desenvolvimento e grava
as versões exatas em `prpm.lock`. Para iniciar o servidor de desenvolvimento com reload:

```powershell
prpm run dev
```

Para iniciar sem reload:

```powershell
prpm run start
```

O arquivo `.env` serve como referência; o Uvicorn não o carrega automaticamente. Defina as
variáveis no ambiente do processo ou carregue-as ao iniciar:

```powershell
$env:GODATA_API_KEY = "uma-chave-aleatoria-com-pelo-menos-24-caracteres"
$env:GODATA_ALLOWED_TARGETS = '{"sqlserver01":["ERP","DataWarehouse"]}'
$env:GODATA_ODBC_DRIVER = "ODBC Driver 18 for SQL Server"
$env:GODATA_ENCRYPT = "true"
$env:GODATA_TRUST_SERVER_CERTIFICATE = "false"

prpm run dev
```

Abra `http://localhost:4400/docs` para consultar o OpenAPI/Swagger.

Em produção, execute esse comando como serviço Windows sob a conta de domínio dedicada. A
conta do serviço precisa ter `Log on as a service`, acesso de rede ao SQL Server e o login
correspondente provisionado no SQL Server. Não use `--reload` em produção.

## Requisição

```http
POST /v1/query HTTP/1.1
Host: godata.interno:4400
X-API-Key: sua-chave
Content-Type: application/json

{
  "server": "sqlserver01",
  "database": "ERP",
  "query": "SELECT TOP (100)\n    id,\n    nome\nFROM dbo.clientes\nWHERE ativo = ?\nORDER BY nome",
  "parameters": [true]
}
```

Exemplo de resposta:

```json
{
  "request_id": "fdcd635b-1913-41cf-956d-64a4f64ea3fa",
  "columns": ["id", "nome"],
  "rows": [[1, "Empresa A"], [2, "Empresa B"]],
  "row_count": 2,
  "truncated": false,
  "elapsed_ms": 18
}
```

Uma query pode ocupar quantas linhas forem necessárias: no JSON, cada quebra de linha é
representada por `\n`. Também é possível usar uma CTE longa. O limite é uma **instrução** por
requisição, não uma linha de texto; `SELECT ...; SELECT ...` continua bloqueado.

Os parâmetros usam marcadores `?` do ODBC. Valores `decimal` são retornados como string para
preservar precisão; datas usam ISO 8601; binários usam Base64. Os dados são retornados como
vetores para preservar colunas duplicadas no resultado.

## Controles incluídos

- `X-API-Key`, comparada em tempo constante;
- allowlist exata de pares servidor/banco;
- apenas uma instrução `SELECT`/CTE por chamada;
- bloqueio de mutações, `SELECT INTO`, comandos e fontes remotas `OPEN*`;
- parâmetros ODBC separados do SQL;
- conexão com `ApplicationIntent=ReadOnly` e atributo ODBC `readonly`;
- rollback e fechamento da conexão após cada chamada;
- timeout de conexão e de consulta;
- limite de linhas, tamanho do SQL e consultas simultâneas;
- `X-Request-ID` para correlação sem registrar a consulta ou os dados.

O banco **não é publicado diretamente** pelo GoData. Restrinja a porta 4400 no firewall aos
clientes autorizados e, fora de uma rede estritamente privada, coloque TLS em um proxy reverso.

## Testes

Os testes não precisam de um SQL Server real:

```powershell
prpm install
prpm run test
```
