import json
import logging
import threading

import chromadb

from ..config import CHROMA_DIR

log = logging.getLogger("vector.chroma")

_lock = threading.Lock()
_client = None
_collection = None

COLLECTION_NAME = "memory_chunks"


def _settings():
    try:
        return chromadb.Settings(anonymized_telemetry=False, allow_reset=True)
    except AttributeError:
        return chromadb.config.Settings(anonymized_telemetry=False)


def get_collection():
    global _client, _collection
    with _lock:
        if _collection is None:
            _client = chromadb.PersistentClient(path=str(CHROMA_DIR), settings=_settings())
            _collection = _client.get_or_create_collection(name=COLLECTION_NAME)
        return _collection


def chroma_health() -> bool:
    try:
        get_collection().count()
        return True
    except Exception:
        return False


def dump_debug():
    col = get_collection()
    return json.dumps({"count": col.count()})
