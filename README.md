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

## Backend (FastAPI + pdfplumber)

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

Backend structure (modular):
- `backend/main.py`: entrypoint for `uvicorn main:app`
- `backend/app/main.py`: API routes and request handling
- `backend/app/services/document_parser.py`: parser routing logic by file type
- `backend/app/parsers/`: file-type specific extractors (`pdf`, `docx`, `text`)

Supported document types:
- `PDF` via `pdfplumber`
- `DOCX` basic text extraction
- Text-like files: `TXT`, `MD`, `CSV`, `JSON`, `YAML`, `XML`, `HTML`

Debug notes:
- Backend logs parser selection, upload metadata, extracted character counts, and full exception traces.
- Frontend logs parse/network errors in browser DevTools Console.
