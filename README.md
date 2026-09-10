# GoData

SDK Python e proxy HTTP para SQL Server com autenticação integrada do Windows. Configure uma vez
e execute T-SQL diretamente por Python, sem montar requisições `requests` ou administrar conexões
ODBC no cliente.

![Aplicação Python → GoData → SQL Server](docs/assets/godata-architecture.png)

```text
Aplicação Python ── API key ──> GoData ── conta Windows/AD ──> SQL Server
```

O cliente não envia nem recebe a credencial do domínio. O GoData não interpreta, filtra ou
restringe o SQL: cada comando é encaminhado ao SQL Server e executado com as permissões da conta
Windows que hospeda o serviço.

## SDK Python: começo rápido

Instale o pacote:

```bash
pip install godata
```

Crie uma engine apontando para a URL do GoData, a API key e o servidor/banco SQL desejados:

```python
from godata import create_engine

engine = create_engine(
    url="https://godata.suaempresa.com",
    api_key="sua-api-key",
    server="sqlserver01\\PRODUCAO",
    database="ERP",
)
```

Use `query` (ou seu alias `execute`) para qualquer T-SQL. Os parâmetros usam `?`, como no ODBC:

```python
result = engine.query(
    "SELECT id, nome FROM dbo.clientes WHERE ativo = ?",
    [True],
)

for cliente in result.mappings():
    print(cliente["id"], cliente["nome"])
```

Alterações seguem a mesma interface e são confirmadas pelo GoData quando bem-sucedidas:

```python
result = engine.execute(
    "UPDATE dbo.clientes SET ativo = ? WHERE id = ?",
    [False, 42],
)
print(result.rows_affected)
```

Também são aceitos procedures, DDL e lotes:

```python
engine.query("EXEC dbo.recalcular_faturas ?", ["2026-09-01"])
engine.query("CREATE TABLE dbo.exemplo (id int PRIMARY KEY); INSERT INTO dbo.exemplo VALUES (1);")
```

`result.rows` contém vetores, `result.mappings()` retorna dicionários, `result.first()` traz a
primeira linha e `result.rows_affected` informa linhas afetadas quando fornecido pelo SQL Server.
Quando um lote retornar múltiplos conjuntos, o primeiro é devolvido.

## Instalação automática no Windows

No PowerShell, instale com:

```powershell
irm https://raw.githubusercontent.com/wanbnn/godata/main/install.ps1 | iex
```

No Prompt de Comando (CMD), use `curl`:

```bat
curl.exe -fsSL https://raw.githubusercontent.com/wanbnn/godata/main/install.bat -o "%TEMP%\godata-install.bat" && call "%TEMP%\godata-install.bat"
```

Ou, quando `wget.exe` estiver disponível:

```bat
wget.exe -q https://raw.githubusercontent.com/wanbnn/godata/main/install.bat -O "%TEMP%\godata-install.bat" && call "%TEMP%\godata-install.bat"
```

O instalador baixa o GoData em `%LOCALAPPDATA%\GoData`, localiza ou instala Python 3.11+, instala
o Microsoft ODBC Driver 18 quando necessário, baixa o `cloudflared`, cria o ambiente virtual e
gera uma API key criptograficamente aleatória. Uma reinstalação preserva a chave; para
substituí-la, baixe o `install.ps1` e execute-o com `-RotateApiKey`.

A instalação automática grava estes padrões:

```env
GODATA_ODBC_DRIVER=ODBC Driver 18 for SQL Server
GODATA_ENCRYPT=true
GODATA_TRUST_SERVER_CERTIFICATE=true
GODATA_CONNECTION_TIMEOUT_SECONDS=2048
GODATA_QUERY_TIMEOUT_SECONDS=0
GODATA_MAX_CONCURRENT_QUERIES=10
```

Ao entrar no Windows, uma janela inicia o GoData e mostra a API key e a nova URL temporária
`trycloudflare.com`. A janela deve permanecer aberta. O instalador tenta registrar o startup,
nesta ordem:

1. tarefa interativa no Agendador de Tarefas;
2. chave `Run` do usuário no Registro;
3. atalho direto para o launcher Python na pasta Startup do usuário.

Todos os métodos são verificados antes de o instalador concluir. Políticas corporativas podem
bloquear CMD, PowerShell, downloads ou todos os mecanismos de startup; essas restrições do
Windows não podem ser contornadas pelo instalador. A instalação do driver ODBC pode apresentar
um prompt UAC, que precisa ser autorizado pelo usuário ou administrador.

## Requisitos

- Windows Server ou Windows 10/11 ingressado no domínio;
- Python 3.11 ou superior;
- Microsoft ODBC Driver 18 for SQL Server;
- `cloudflared` instalado e disponível no `PATH`;
- uma conta AD dedicada, com as permissões necessárias nos bancos acessados.

> As consultas são executadas com as permissões da conta AD do serviço. Restrinja essa conta
> ao mínimo necessário e não exponha a API para redes não confiáveis.

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
$env:GODATA_TRUST_SERVER_CERTIFICATE = "true"

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

## API HTTP (opcional)

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
  "rows_affected": 0,
  "truncated": false,
  "elapsed_ms": 18
}
```

Uma query pode ocupar quantas linhas forem necessárias: no JSON, cada quebra de linha é
representada por `\n`. Não há whitelist, parser ou limite de tamanho de SQL no GoData: são
aceitos comandos T-SQL de leitura e escrita, procedures, DDL e lotes com múltiplas instruções.
Para comandos que não retornam dados, `columns` e `rows` ficam vazios e `rows_affected` informa
o total de linhas afetadas quando o SQL Server o disponibiliza.
Quando um lote produz mais de um conjunto de resultados, a resposta contém o primeiro deles.

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

## Controles operacionais

- `X-API-Key`, comparada em tempo constante;
- parâmetros ODBC separados do SQL;
- conexão com `ApplicationIntent=ReadWrite` e commit após execução bem-sucedida;
- rollback e fechamento da conexão em caso de falha;
- timeout de conexão e de consulta;
- limite configurável de consultas simultâneas;
- `X-Request-ID` para correlação sem registrar a consulta ou os dados.

O banco **não é publicado diretamente** pelo GoData. O servidor HTTP fica acessível apenas no
loopback e o Cloudflare Tunnel fornece a conexão externa com TLS, sem expor a porta 4400.

Por padrão, `GODATA_QUERY_TIMEOUT_SECONDS=0` permite que a consulta termine sem um limite
artificial de execução. Defina um valor positivo para impor um limite. O
`GODATA_CONNECTION_TIMEOUT_SECONDS` continua limitando separadamente a abertura da conexão.

## Testes

Os testes não precisam de um SQL Server real:

```powershell
prpm install
prpm run test
```
