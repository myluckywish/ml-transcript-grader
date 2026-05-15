# Screening Automation

Minimal Next.js frontend with a Python document parser backend.

## Frontend (Next.js)

```bash
npm install
npm run dev
```

To run frontend + parser backend together:

```bash
npm run dev:stack
```

`dev:stack` expects backend dependencies to be installed in `backend/.venv`.

By default, the frontend posts documents to `http://127.0.0.1:8000/parse`.
Override this with:

```bash
NEXT_PUBLIC_PARSER_API_URL=http://127.0.0.1:8000/parse npm run dev
```

## Backend (FastAPI)

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

For verbose debugging logs:

```bash
uvicorn main:app --reload --port 8000 --log-level debug
```

Available endpoints:
- `GET /health`
- `POST /parse` (multipart form with `file`)
- `POST /transcript/analyze` (multipart form with `file`)
- `POST /transcript/analyze/submit` (multipart form with `file`, returns `job_id`)
- `GET /transcript/jobs/{job_id}` (poll transcript analysis status/result)
- `POST /transcript/batches/submit` (multipart form with `files`, returns `batch_id` + job list)
- `GET /transcript/batches/{batch_id}` (poll batch progress + per-job results)
- `GET /units/taxonomy`
- `POST /units/mappings/upsert`
- `POST /units/classify-titles`
- `GET /units/unknowns`
- `POST /units/unknowns/{unknown_id}/resolve`

`POST /parse` response shape:

```json
{
  "filename": "example.json",
  "mime_type": "application/json",
  "extracted_text": "{\n  \"name\": \"demo\"\n}",
  "characters": 22,
  "parsed_content": {
    "content_kind": "structured_json",
    "json": { "name": "demo" },
    "text": "{\n  \"name\": \"demo\"\n}",
    "lines": ["{", "  \"name\": \"demo\"", "}"],
    "paragraphs": ["{\n  \"name\": \"demo\"\n}"]
  }
}
```

`parsed_content.content_kind` is:
- `structured_json` for valid JSON input (`parsed_content.json` populated)
- `plain_text` for all other files (`parsed_content.json` is `null`)

Azure OpenAI scaffold for transcript analysis:

Set these environment variables in your shell (or `backend/.env` if your process loader supports it):

```bash
AZURE_DOC_INTEL_ENABLED=false
AZURE_DOC_INTEL_ENDPOINT=
AZURE_DOC_INTEL_API_KEY=
AZURE_DOC_INTEL_API_VERSION=2024-11-30
AZURE_DOC_INTEL_MODEL_ID=prebuilt-layout
AZURE_DOC_INTEL_POLL_INTERVAL_SECONDS=1.0
AZURE_DOC_INTEL_TIMEOUT_SECONDS=45
TRANSCRIPT_WORKERS=10

AZURE_OPENAI_ENABLED=false
AZURE_OPENAI_ENDPOINT=
AZURE_OPENAI_API_KEY=
AZURE_OPENAI_API_VERSION=2024-10-21
AZURE_OPENAI_DEPLOYMENT=
AZURE_OPENAI_TEMPERATURE=0
AZURE_OPENAI_TIMEOUT_SECONDS=45
```

Frontend timeout (optional):

```bash
NEXT_PUBLIC_ANALYZE_POLL_INTERVAL_MS=1500
NEXT_PUBLIC_ANALYZE_MAX_WAIT_MS=600000
```

When `AZURE_DOC_INTEL_ENABLED=true` and settings are configured, `/transcript/analyze` uses Azure Document Intelligence for text extraction.
If parsing fails, analysis stops and the API returns an error.
When `AZURE_OPENAI_ENABLED=false`, `/transcript/analyze` still returns extracted text plus provider configuration status.
Once Azure access is available, set:
- `AZURE_DOC_INTEL_ENABLED=true`
- DI endpoint and key
- `AZURE_OPENAI_ENABLED=true`
- endpoint, key, deployment

Smoke test:

```bash
curl -X POST http://127.0.0.1:8000/transcript/analyze \
  -F "file=@/absolute/path/to/transcript.pdf"
```

Academic unit classification workflow:

1) Classify course titles (known mappings + rules + unknown queue):

```bash
curl -X POST http://127.0.0.1:8000/units/classify-titles \
  -H "Content-Type: application/json" \
  -d '{
    "school_id": "school_123",
    "titles": ["Alg II H", "Physics", "English 11", "US GOV", "Intro to Aerospace"]
  }'
```

2) List unknown titles needing review:

```bash
curl "http://127.0.0.1:8000/units/unknowns?school_id=school_123&status=open&limit=50"
```

3) Resolve an unknown and automatically create a reusable mapping:

```bash
curl -X POST http://127.0.0.1:8000/units/unknowns/1/resolve \
  -H "Content-Type: application/json" \
  -d '{"subject":"other_units","note":"School-specific STEM elective","create_mapping":true}'
```

4) Manually seed/override mappings for known titles:

```bash
curl -X POST http://127.0.0.1:8000/units/mappings/upsert \
  -H "Content-Type: application/json" \
  -d '{"school_id":"school_123","raw_title":"US GOV","subject":"social_sciences","source":"manual","confidence":1.0}'
```

Step-by-step debugging:

- Add `?debug=true` to these endpoints to return ordered debug steps with elapsed milliseconds:
  - `/parse`
  - `/units/classify-titles`
  - `/units/mappings/upsert`
  - `/units/unknowns`
  - `/units/unknowns/{unknown_id}/resolve`

Backend structure (modular):
- `backend/main.py`: entrypoint for `uvicorn main:app`
- `backend/app/main.py`: API routes and request handling
- `backend/app/services/transcript_pipeline.py`: transcript extraction + AI analysis pipeline
- `backend/app/services/transcript_jobs.py`: background job queue and batch tracking

Supported document types:
- `PDF`, `DOCX`, and image/text formats supported by Azure Document Intelligence configuration

Debug notes:
- Backend logs parser selection, upload metadata, extracted character counts, and full exception traces.
- Frontend logs parse/network errors in browser DevTools Console.
