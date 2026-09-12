# Memory Engine — Windows Setup Guide

This guide is for someone who is comfortable installing applications and copying commands, but does not work with Python or databases every day.

By the end, the Memory Engine will be running at:

~~~text
http://localhost:8000
~~~

The application uses four local pieces:

| Component | What it does | How it runs |
|---|---|---|
| FastAPI + Python | Runs the application and API | Windows terminal |
| Neo4j | Stores entities, relationships, and graph history | Docker container |
| PostgreSQL | Stores documents, audits, and query logs | Docker container |
| ChromaDB | Stores searchable text chunks and embeddings | Local files in `data/chroma` |

## 1. Install the required Windows software

Install these applications using their official installers:

1. Git for Windows: https://git-scm.com/download/win
2. Python 3.11 or newer: https://www.python.org/downloads/windows/
3. Docker Desktop: https://www.docker.com/products/docker-desktop/

During Python installation, enable:

~~~text
Add python.exe to PATH
~~~

During Docker Desktop installation, enable the WSL 2 backend if Docker offers that option. Restart Windows if the installer asks you to.

Open PowerShell and verify the installations:

~~~powershell
git --version
py --version
docker --version
docker compose version
~~~

If any command is not recognized, close PowerShell, open a new PowerShell window, and try again.

## 2. Download the repository

Replace the placeholder repository URL with the real GitHub URL supplied by the project owner.

~~~powershell
cd $HOME
git clone https://github.com/OWNER/REPOSITORY.git wespa
cd $HOME\wespa
~~~

If the repository has already been downloaded:

~~~powershell
cd $HOME\wespa
git pull
~~~

Confirm that the project root is correct:

~~~powershell
Get-ChildItem
~~~

You should see `app`, `configs`, `data`, `docker-compose.yml`, `frontend`, `requirements.txt`, and `scripts`.

## 3. Start Neo4j and PostgreSQL

From the repository root:

~~~powershell
docker compose up -d
docker compose ps
~~~

Both services should eventually show as running or healthy:

~~~text
memory-neo4j
memory-postgres
~~~

Useful database interfaces:

- Neo4j Browser: http://localhost:7474
- Neo4j username: `neo4j`
- Neo4j password: `memoryengine2025`
- PostgreSQL port: `5432`

## 4. Create the Python environment

~~~powershell
py -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
~~~

When activation works, the terminal prompt normally begins with `(.venv)`.

## 5. Create the environment configuration

~~~powershell
Copy-Item .env.example .env
notepad .env
~~~

For the first setup, use offline mode:

~~~text
SKIP_LLM=true
~~~

The database settings should remain:

~~~text
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=memoryengine2025

POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=knowledge_memory
POSTGRES_USER=postgres
POSTGRES_PASSWORD=memoryengine2025
~~~

Offline mode does not require an LLM API key. It uses deterministic extraction and is the safest mode for a first demo.

### Optional: enable LLM answers and extraction

If an API key has been provided by the project owner, set it in `.env`:

~~~text
GROQ_API_KEY=replace_with_real_key
SKIP_LLM=false
~~~

Never commit `.env` or send API keys through GitHub, email, or screenshots.

## 6. Build the initial memory

Use the deterministic bootstrap first:

~~~powershell
python scripts/bootstrap_memory.py
~~~

A successful run ends with a message similar to:

~~~text
bootstrap complete: seen=... added=... entities=+... rels=+...
~~~

The bootstrap is safe to run again. Existing documents are skipped when they are duplicates.

Optional LLM-driven build:

~~~powershell
python scripts/build_memory.py
~~~

Only use that command after adding a valid LLM key to `.env`.

## 7. Start the application

Keep Docker Desktop running. In the activated virtual environment:

~~~powershell
python -m uvicorn app.main:fastapi_app --host 0.0.0.0 --port 8000 --reload
~~~

Leave this terminal open. Open a browser and visit:

~~~text
http://localhost:8000
~~~

## 8. Confirm that everything is healthy

Open a second PowerShell window:

~~~powershell
cd $HOME\wespa
.\.venv\Scripts\Activate.ps1
Invoke-RestMethod http://localhost:8000/health | ConvertTo-Json
Invoke-RestMethod http://localhost:8000/stats | ConvertTo-Json
~~~

The health response should report `postgres`, `neo4j`, `chroma`, and `all_ready` as `true`.

## 9. Try the application

Open `http://localhost:8000` and ask:

~~~text
Which companies did people who left OpenAI go on to found?
~~~

Other demo questions:

~~~text
How did Google end up re-hiring Noam Shazeer?
What is Safe Superintelligence valued at?
~~~

Keep `Hybrid` selected for the full experience. It combines graph facts and semantic passages, then shows sources, confidence, and the graph path used.

## 10. Ingest a new source

Use the **Ingest new source** button in the UI, or use PowerShell directly:

~~~powershell
$body = @{ mode = "url"; url = "https://example.com/article" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8000/ingest -ContentType "application/json" -Body $body | ConvertTo-Json -Depth 10
~~~

Duplicate documents are skipped.

## 11. Run evaluation and tests

~~~powershell
python scripts/run_evaluation.py
python -m pytest tests -v
~~~

Evaluation output is written to:

~~~text
data\eval\results.json
~~~

## 12. Stop and restart

Stop the Python server with `Ctrl+C`.

Stop Docker services without deleting data:

~~~powershell
docker compose stop
~~~

Start them later:

~~~powershell
docker compose start
~~~

Remove containers but preserve named volumes:

~~~powershell
docker compose down
docker compose up -d
~~~

Do not use this unless you intentionally want to delete database volumes:

~~~powershell
docker compose down -v
~~~

## 13. Troubleshooting

### Docker is not running

~~~powershell
docker info
docker compose ps
docker compose up -d
~~~

### Port 8000 is already in use

~~~powershell
Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue
Stop-Process -Id <PROCESS_ID>
~~~

Only stop the process if it belongs to this project.

### Port 5432, 7474, or 7687 is already in use

~~~powershell
Get-NetTCPConnection -LocalPort 5432,7474,7687 -ErrorAction SilentlyContinue
~~~

Stop the conflicting application or change the Docker port mapping and matching `.env` values.

### The page opens but memory is empty

~~~powershell
python scripts/bootstrap_memory.py
~~~

Then restart the Python server and refresh the browser.

### LLM rate limits or missing keys

~~~powershell
notepad .env
~~~

Set:

~~~text
SKIP_LLM=true
~~~

Restart Uvicorn after changing `.env`.

### View service logs

~~~powershell
docker compose logs --tail 100 neo4j
docker compose logs --tail 100 postgres
docker compose logs -f
~~~

## 14. Important data locations

~~~text
data\raw\                 Included source documents and fixtures
data\processed\           Processed seed facts
data\chroma\              Local ChromaDB files
data\eval\results.json    Latest evaluation output
.env                      Local secrets and database configuration
~~~

The `.env` file and `data/chroma` directory are ignored by Git. Do not commit credentials or local database data.
