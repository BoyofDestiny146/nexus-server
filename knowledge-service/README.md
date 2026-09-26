# NEXUS Knowledge Fabric — retrieval service

Extracts, chunks, embeds, and indexes Knowledge Base sources. CareConnect API
owns MariaDB metadata, authorization, and search orchestration. This service
never talks to XiaoZhi and never executes Revel.

## Run locally

```sh
cd knowledge-service
pip install -e .
KS_SOURCE_DIR=/tmp/knowledge-sources \
  KS_QDRANT_URL=http://127.0.0.1:6333 \
  KS_OLLAMA_URL=http://127.0.0.1:11434 \
  uvicorn knowledge_service.main:app --host 0.0.0.0 --port 8090
```

## Embeddings

Uses local Ollama `nomic-embed-text` (768-d, ~274 MB). The service **does not**
pull models. If the model is missing, indexing returns HTTP 503 with:

```
ollama pull nomic-embed-text
```

## Tests

```sh
cd knowledge-service
python -m pytest -q
```
