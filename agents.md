# Memory Engine — Agent / Codex Runbook

This file is for an AI coding agent or technical operator working on a Windows machine. It contains reliable commands for inspecting, starting, testing, and troubleshooting the project.

## Project assumptions

~~~text
Repository root: C:\Users\<USER>\wespa
API/UI: http://localhost:8000
Neo4j Browser: http://localhost:7474
Neo4j Bolt: bolt://localhost:7687
PostgreSQL: localhost:5432
ChromaDB: .\data\chroma
~~~

Local Docker defaults:

- Neo4j username: neo4j
- Neo4j password: memoryengine2025
- PostgreSQL username: postgres
- PostgreSQL password: memoryengine2025
- PostgreSQL database: knowledge_memory

Treat .env as secret configuration. Do not print it, commit it, or paste API keys into logs.

## 1. Confirm the Windows toolchain

~~~powershell
git --version
py --version
docker --version
docker compose version
~~~

If Docker commands fail, start Docker Desktop and wait until Docker Engine is running.

## 2. Clone or update the repository

For a new checkout, replace the placeholder repository URL with the real GitHub URL:

~~~powershell
cd $HOME
git clone https://github.com/OWNER/REPOSITORY.git wespa
cd $HOME\wespa
~~~

For an existing checkout:

~~~powershell
cd $HOME\wespa
git status --short
git pull
~~~

Before making changes:

~~~powershell
git branch --show-current
git status --short
~~~

Preserve existing user changes. Do not reset or clean the repository unless explicitly requested.

## 3. Create and activate the Python environment

~~~powershell
cd $HOME\wespa
py -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
~~~

Confirm that the shell is using the project environment:

~~~powershell
python -c "import sys; print(sys.executable)"
~~~

The printed path should end in .venv\Scripts\python.exe.

## 4. Create local configuration

Create .env only if it does not already exist:

~~~powershell
cd $HOME\wespa
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
~~~

For a deterministic offline run, disable LLM calls:

~~~powershell
(Get-Content .env) -replace '^SKIP_LLM=.*$', 'SKIP_LLM=true' | Set-Content .env
~~~

For online answer generation, set a supported provider key in .env and use SKIP_LLM=false. Never echo the file after adding secrets.

The local database settings must match Docker Compose:

~~~text
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=memoryengine2025
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=knowledge_memory
POSTGRES_USER=postgres
POSTGRES_PASSWORD=memoryengine2025
CHROMA_PERSIST_DIRECTORY=./data/chroma
~~~

## 5. Start and verify Docker services

~~~powershell
cd $HOME\wespa
docker compose up -d
docker compose ps
~~~

Check the two databases directly:

~~~powershell
docker exec memory-postgres pg_isready -U postgres -d knowledge_memory
docker exec memory-neo4j cypher-shell -u neo4j -p memoryengine2025 "RETURN 1"
~~~

Expected results contain accepting connections and 1.

If a container is unhealthy:

~~~powershell
docker compose logs --tail=100 postgres
docker compose logs --tail=100 neo4j
~~~

## 6. Build or repair local memory

The offline bootstrap is the safest first run because it does not require an LLM key:

~~~powershell
cd $HOME\wespa
.\.venv\Scripts\Activate.ps1
python scripts/bootstrap_memory.py
~~~

Use normal LLM-backed ingestion only when provider configuration is valid:

~~~powershell
python scripts/build_memory.py
~~~

The ingestion scripts populate the graph, PostgreSQL metadata, and embedded Chroma collection. They are intended to be repeatable; inspect counts after a run instead of deleting volumes.

## 7. Start the API and UI

Run the server from the repository root:

~~~powershell
cd $HOME\wespa
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:fastapi_app --host 0.0.0.0 --port 8000 --reload
~~~

Keep this terminal open. In a second PowerShell window:

~~~powershell
Invoke-RestMethod http://localhost:8000/openapi.json | Out-Null
Invoke-RestMethod http://localhost:8000/health | ConvertTo-Json
Invoke-RestMethod http://localhost:8000/stats | ConvertTo-Json
~~~

Open http://localhost:8000 in the browser.

If the browser shows stale UI or stale API fields, hard-refresh. If the server started before a code or .env change, stop it with Ctrl+C and start it again.

## 8. Execute a query from PowerShell

This smoke test avoids live web fetching and LLM generation:

~~~powershell
$body = @{ question = "Which companies did people who left OpenAI go on to found?"; retrieval_mode = "hybrid"; allow_live = $false; skip_llm = $true } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8000/query -ContentType "application/json" -Body $body | ConvertTo-Json -Depth 20
~~~

For a normal answer-generation run, change skip_llm to $false and use a configured provider.

CLI equivalent:

~~~powershell
python scripts/run_query.py "Which companies did people who left OpenAI go on to found?" --mode hybrid
~~~

## 9. Diagnose graph traversal

Run a query and store the complete response:

~~~powershell
$body = @{ question = "Tell me about the Spanish flu"; retrieval_mode = "hybrid"; allow_live = $true; skip_llm = $true } | ConvertTo-Json
$result = Invoke-RestMethod -Method Post -Uri http://localhost:8000/query -ContentType "application/json" -Body $body
$result | ConvertTo-Json -Depth 30
$result.graph_debug | ConvertTo-Json -Depth 30
$result.graph_path | ConvertTo-Json -Depth 30
~~~

Interpret the fields:

- graph_debug.document_ids empty: retrieval did not identify graph-backed documents.
- graph_debug.mention_edges zero: provenance was not connected to graph entities, or the graph has not been populated.
- graph_path.nodes and graph_path.edges empty: no graph material was available for the response, even if web sources were fetched.
- graph_debug.facts zero with mention edges present: entities were found, but no relationship facts were retrieved.
- Web sources alone do not automatically create a traversal. Confirm that fetched content was ingested and that the response contains graph-backed document IDs.

Inspect the browser response and server terminal together. The server log is authoritative for whether retrieval, ingestion, and traversal happened.

## 10. Inspect PostgreSQL

Open an interactive SQL shell:

~~~powershell
docker exec -it memory-postgres psql -U postgres -d knowledge_memory
~~~

Useful SQL:

~~~sql
\dt
SELECT COUNT(*) AS sources FROM sources;
SELECT COUNT(*) AS documents FROM documents;
SELECT COUNT(*) AS ingestion_logs FROM ingestion_logs;
SELECT COUNT(*) AS fact_audit_rows FROM fact_audit;
SELECT COUNT(*) AS queries FROM queries;
SELECT COUNT(*) AS answer_facts FROM answer_facts;
\q
~~~

One-off commands:

~~~powershell
docker exec memory-postgres psql -U postgres -d knowledge_memory -c "SELECT COUNT(*) FROM sources;"
docker exec memory-postgres psql -U postgres -d knowledge_memory -c "SELECT id, title, url FROM sources ORDER BY id DESC LIMIT 10;"
~~~

## 11. Inspect Neo4j

Open http://localhost:7474 and sign in with the local credentials above.

Or use Cypher shell:

~~~powershell
docker exec -it memory-neo4j cypher-shell -u neo4j -p memoryengine2025
~~~

Useful Cypher:

~~~cypher
MATCH (n) RETURN labels(n) AS labels, count(n) AS count ORDER BY count DESC;
MATCH ()-[r]->() RETURN type(r) AS relationship, count(r) AS count ORDER BY count DESC;
MATCH (d:Document) RETURN d LIMIT 10;
MATCH (d:Document)-[r:MENTIONS]->(e) RETURN d, r, e LIMIT 25;
MATCH (a)-[r]->(b) RETURN a, r, b LIMIT 25;
:exit
~~~

One-off count:

~~~powershell
docker exec memory-neo4j cypher-shell -u neo4j -p memoryengine2025 "MATCH (n) RETURN count(n) AS nodes"
~~~

## 12. Inspect ChromaDB

ChromaDB is embedded in the API process; it is not a separate Docker container. Persistent data is under data\chroma.

Inspect files and approximate size:

~~~powershell
Get-ChildItem .\data\chroma -Recurse -File
(Get-ChildItem .\data\chroma -Recurse -File | Measure-Object -Property Length -Sum).Sum
~~~

Check the collection and document count:

~~~powershell
python -c "from app.vector.chroma_client import get_collection; c=get_collection(); print('collection=', c.name, 'count=', c.count())"
~~~

Peek at stored metadata:

~~~powershell
python -c "from app.vector.chroma_client import get_collection; c=get_collection(); print(c.peek(3))"
~~~

If the directory is missing or the count is zero, run python scripts/bootstrap_memory.py and check API health again.

## 13. Add a URL for ingestion

Run only when the server is already running:

~~~powershell
$body = @{ url = "https://example.com/article" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8000/ingest/url -ContentType "application/json" -Body $body | ConvertTo-Json -Depth 20
~~~

For the deterministic fixture path:

~~~powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8000/ingest/fixtures -ContentType "application/json" -Body "{}" | ConvertTo-Json -Depth 20
~~~

## 14. Run tests and evaluation

~~~powershell
cd $HOME\wespa
.\.venv\Scripts\Activate.ps1
python -m pytest tests -v
python scripts/run_evaluation.py
~~~

Focused test while debugging:

~~~powershell
python -m pytest tests -k "graph or retrieval or query" -v
~~~

Run tests after changing retrieval, ingestion, graph serialization, API schemas, or frontend response handling.

## 15. Logs, ports, and process checks

~~~powershell
docker compose logs --tail=200
docker compose logs --tail=200 postgres
docker compose logs --tail=200 neo4j
~~~

Check which process owns each port:

~~~powershell
Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue
Get-NetTCPConnection -LocalPort 7474 -ErrorAction SilentlyContinue
Get-NetTCPConnection -LocalPort 7687 -ErrorAction SilentlyContinue
Get-NetTCPConnection -LocalPort 5432 -ErrorAction SilentlyContinue
~~~

Stop only the foreground API with Ctrl+C. Stop infrastructure without deleting data:

~~~powershell
docker compose stop
docker compose start
~~~

Recreate containers while keeping named volumes:

~~~powershell
docker compose down
docker compose up -d
~~~

Never use docker compose down -v during normal debugging. It deletes the named Neo4j and PostgreSQL volumes and can erase project memory.

## 16. Agent safety checklist

Before changing code:

- Work from the repository root.
- Check git status --short.
- Activate .venv.
- Confirm Docker services with docker compose ps.
- Confirm /health before diagnosing application behavior.
- Read the existing API schema and frontend response contract before changing fields.
- Do not print .env or expose provider keys.
- Do not delete data\chroma or Docker volumes to fix a query.
- Do not stop unrelated processes; identify the owning PID first.
- After changes, run the narrowest relevant tests and then the full test command when practical.
- Report changed files, verification commands, and any remaining environment-specific issue.

