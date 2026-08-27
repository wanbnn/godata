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
- `cloudflared` instalado e disponível no `PATH`;
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

O GoData carrega automaticamente o arquivo `.env` da pasta em que o comando é executado.
Variáveis definidas diretamente no ambiente do processo têm precedência sobre o arquivo.
Se preferir, defina-as no PowerShell antes de iniciar:

```powershell
$env:GODATA_API_KEY = "uma-chave-aleatoria-com-pelo-menos-24-caracteres"
$env:GODATA_ODBC_DRIVER = "ODBC Driver 18 for SQL Server"
$env:GODATA_ENCRYPT = "true"
$env:GODATA_TRUST_SERVER_CERTIFICATE = "false"

prpm run dev
```

Abra `http://localhost:4400/docs` para consultar o OpenAPI/Swagger.

## Cloudflare Tunnel

O GoData escuta apenas em `127.0.0.1:4400`; o acesso externo deve passar pelo
[`cloudflared`](https://github.com/cloudflare/cloudflared). Para criar um endereço temporário
de teste, mantenha `prpm run dev` ou `prpm run start` em execução e, em outro terminal, rode:

```powershell
prpm run tunnel
```

O comando exibe uma URL aleatória `https://*.trycloudflare.com`. Esse modo não exige conta,
mas é destinado somente a testes.

Para produção, autentique e crie um túnel nomeado:

```powershell
cloudflared tunnel login
cloudflared tunnel create godata
cloudflared tunnel route dns godata godata.seudominio.com
Copy-Item cloudflared/config.yml.example cloudflared/config.yml
```

Edite `cloudflared/config.yml` com o UUID do túnel, o caminho do arquivo de credenciais e o
hostname criado. O arquivo local é ignorado pelo Git. Depois, com o GoData em execução, inicie
o túnel:

```powershell
prpm run tunnel-prod
```

Em produção, configure `prpm run start` e `prpm run tunnel-prod` como serviços Windows sob as
contas apropriadas. A porta 4400 não precisa ser liberada no firewall, pois o `cloudflared`
estabelece uma conexão de saída com a Cloudflare. Mantenha a exigência de `X-API-Key` e, se o
endpoint não for público, aplique também uma política do Cloudflare Access.

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

## Discovery de metadados

Os endpoints de discovery exigem o mesmo header `X-API-Key` e mostram somente objetos visíveis
para a conta Windows que executa o GoData:

```text
GET /v1/discovery/databases?server=sqlserver01
GET /v1/discovery/schemas?server=sqlserver01&database=ERP
GET /v1/discovery/tables?server=sqlserver01&database=ERP&schema=dbo
GET /v1/discovery/columns?server=sqlserver01&database=ERP&schema=dbo&table=clientes
```

O parâmetro `schema` em `/tables` é opcional; sem ele, tabelas e views de todos os schemas
visíveis são retornadas. Os endpoints também estão disponíveis para teste interativo em `/docs`.

## Controles incluídos

- `X-API-Key`, comparada em tempo constante;
- apenas uma instrução `SELECT`/CTE por chamada;
- bloqueio de mutações, `SELECT INTO`, comandos e fontes remotas `OPEN*`;
- parâmetros ODBC separados do SQL;
- conexão com `ApplicationIntent=ReadOnly` e atributo ODBC `readonly`;
- rollback e fechamento da conexão após cada chamada;
- timeout de conexão e de consulta;
- limite de linhas, tamanho do SQL e consultas simultâneas;
- `X-Request-ID` para correlação sem registrar a consulta ou os dados.

O banco **não é publicado diretamente** pelo GoData. O servidor HTTP fica acessível apenas no
loopback e o Cloudflare Tunnel fornece a conexão externa com TLS, sem expor a porta 4400.

## Testes

Os testes não precisam de um SQL Server real:

```powershell
prpm install
prpm run test
```
