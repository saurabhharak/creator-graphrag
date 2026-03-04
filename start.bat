@echo off
REM ============================================================
REM Creator GraphRAG -- Single-command startup (Windows)
REM Usage:  start.bat           (starts everything)
REM         start.bat --backend (starts API + worker only)
REM         start.bat --frontend(starts frontend only)
REM ============================================================

set ROOT=%~dp0
cd /d "%ROOT%"

echo.
echo  +-------------------------------------------+
echo  ^|  Creator GraphRAG -- Starting Services    ^|
echo  +-------------------------------------------+
echo.

REM -- 1. Docker services ----------------------------------------
echo [1/7] Checking Docker services...
docker info >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo  ERROR: Docker is not running. Start Docker Desktop first.
    pause
    exit /b 1
)

REM docker-compose up is idempotent: creates if missing, starts if stopped, no-op if running.
echo  Starting project Docker services (postgres, redis, qdrant, neo4j, minio)...
docker-compose up -d postgres redis qdrant neo4j minio
if %ERRORLEVEL% neq 0 (
    echo  ERROR: docker-compose up failed. Check for port conflicts.
    echo  TIP: Other projects may be using ports 5432, 6333, 7474, 7687.
    echo       Stop conflicting containers first:  docker stop [container-name]
    pause
    exit /b 1
)
echo  Waiting for services to become healthy...
timeout /t 12 /nobreak >nul
echo  Docker services ready.

REM -- 2. Auto-restore DBs if empty ---------------------------------
echo [2/7] Checking databases for data...
python "%ROOT%scripts\db_manager.py" auto-restore
echo  Database check complete.

REM -- 3. Check Ollama -------------------------------------------
echo [3/7] Checking Ollama...
curl -s http://localhost:11434/api/tags >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo  WARNING: Ollama is not running on port 11434.
    echo           Embeddings will use HuggingFace if HF_TOKEN is set,
    echo           otherwise search/video generation will fail.
    echo           To start Ollama:  ollama serve
    echo.
) else (
    echo  Ollama is running.
)

REM -- 4. Frontend deps ------------------------------------------
echo [4/7] Checking frontend dependencies...
if not exist "%ROOT%apps\web\node_modules" (
    echo  Installing npm packages...
    cd /d "%ROOT%apps\web"
    npm install
    cd /d "%ROOT%"
) else (
    echo  Frontend dependencies already installed.
)

REM -- 5. Start Backend ------------------------------------------
if "%1"=="--frontend" goto :start_frontend
echo [5/7] Starting Backend (FastAPI) on port 8000...
REM NOTE: --reload is for development only. For production use: --workers 4
start "Creator-GraphRAG-Backend" cmd /k "cd /d %ROOT%apps\api && uvicorn app.main:app --reload --port 8000"
timeout /t 3 /nobreak >nul

REM -- 6. Start Worker -------------------------------------------
echo [6/7] Starting Worker (Celery)...
start "Creator-GraphRAG-Worker" cmd /k "cd /d %ROOT%apps\worker && celery -A app.worker worker --loglevel=info -Q default,ocr,embed,graph -c 4"
timeout /t 2 /nobreak >nul

REM -- 7. Start Frontend -----------------------------------------
:start_frontend
if "%1"=="--backend" goto :done
echo [7/7] Starting Frontend (Vite) on port 3000...
start "Creator-GraphRAG-Frontend" cmd /k "cd /d %ROOT%apps\web && npm run dev"

:done
echo.
echo  +-------------------------------------------------------+
echo  ^|  All services started!                                ^|
echo  ^|                                                       ^|
echo  ^|  Backend API:    http://localhost:8000                 ^|
echo  ^|  Swagger Docs:   http://localhost:8000/docs            ^|
echo  ^|  Worker Queue:   Celery (default, ocr, embed, graph)  ^|
echo  ^|  Frontend UI:    http://localhost:3000                 ^|
echo  ^|  Neo4j Browser:  http://localhost:7474                 ^|
echo  ^|  MinIO Console:  http://localhost:9001                 ^|
echo  ^|                                                       ^|
echo  ^|  Helpful commands:                                    ^|
echo  ^|    python scripts\db_manager.py backup                ^|
echo  ^|    python scripts\db_manager.py restore               ^|
echo  ^|    python scripts\db_manager.py status                ^|
echo  +-------------------------------------------------------+
echo.
echo  Press any key to close this window (services keep running)
pause >nul
