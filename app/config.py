import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

with open(ROOT / "configs" / "settings.yaml") as f:
    SETTINGS = yaml.safe_load(f)

DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
CHROMA_DIR = DATA_DIR / "chroma"
EVAL_RESULTS_PATH = DATA_DIR / "eval" / "results.json"
FRONTEND_DIR = ROOT / "frontend"

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "memoryengine2025")

POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_DB = os.getenv("POSTGRES_DB", "knowledge_memory")
POSTGRES_USER = os.getenv("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "memoryengine2025")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", SETTINGS["llm"]["model"])
SKIP_LLM = os.getenv("SKIP_LLM", "false").lower() in ("true", "1", "yes")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-oss-120b")

CHUNK_SIZE = SETTINGS["chunking"]["size"]
CHUNK_OVERLAP = SETTINGS["chunking"]["overlap"]
TOP_K_PASSAGES = SETTINGS["retrieval"]["top_k_passages"]
MAX_GRAPH_FACTS = SETTINGS["retrieval"]["max_graph_facts"]
MAX_HOPS = SETTINGS["retrieval"]["max_hops"]
MAX_PATHS = SETTINGS["retrieval"]["max_paths"]
SUPERSEDE_WINDOW_DAYS = SETTINGS["temporal"]["supersede_window_days"]
MAX_DOC_CHARS = SETTINGS["extraction"]["max_document_chars"]
ENTITY_TYPES = SETTINGS["extraction"]["entity_types"]
ALLOWED_RELATIONS = SETTINGS["extraction"]["allowed_relations"]
DEFAULT_RELIABILITY = SETTINGS["trust"]["default_reliability"]
SOURCE_WEIGHTS = SETTINGS["trust"]["source_weights"]
