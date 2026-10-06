# Attendance Assistant (Flask + LangGraph + Chroma)

Upload attendance evidence in any format → it is normalised into one canonical schema → ask questions in chat → every answer shows the **source evidence** (file + sheet/row, table/row, page/lines or OCR lines). Per-user accounts, per-user data, feedback loop, CSV/XLSX export.

## Structure (6 code files)
```
app.py              Flask routes: auth, upload, chat, feedback, export
core.py             parsing + OCR, SQLite, Chroma, LangGraph pipeline
static/             index.html, app.js, style.css  (vanilla)
generate_samples.py sample files for every format + canonical schema + expected results
tests/test_app.py   unit / integration / security tests
```

## Architecture
```
upload ─► parse ─► table headers recognised? ──yes──► canonical records (deterministic, no LLM)
         │ (CSV/XLSX/DOCX tables/PDF text/PDF scan/images)      no
         │  scans: RapidOCR (open-source, ONNX)  ──────────► LLM normalises text → canonical records
         └─► text chunks ─► OpenAI embeddings ─► Chroma (one collection per user)

question ─► LangGraph:  plan ─► compute ─┬─ result ─────────────► answer
                        (LLM→JSON intent) (pandas, exact maths)  └─ none ─► retrieve (Chroma) ─► answer
answer prompt = rules + COMPUTED RESULT + evidence rows + excerpts + this user's past feedback
```
* **Canonical schema** (`samples/canonical_schema.json`): `employee_id, employee_name, department, date (YYYY-MM-DD), status (Present|Absent|Leave), file, ref`. Identity = lower-cased name; re-upload of the same employee+date overwrites.
* **Attendance % = Present / (Present + Absent + Leave).** Ties are reported, not hidden.
* **Numbers are never computed by the LLM** – the planner only picks an operation; pandas does the maths, so averages/rankings are exact and reproducible.
* **"Not in data"**: no matching records and no relevant excerpt → fixed "data does not contain the answer" reply, no sources. If excerpts exist the model must set `found=false` rather than guess; sources are only returned when `found=true`.
* **Feedback**: 👍/👎 + optional comment per answer, stored per user; the last 10 relevant items are appended to the system prompt on every new question (runtime, no retraining).

## Technology choices
Flask (simple), SQLite (users/records/chat/feedback – zero setup), **ChromaDB** persistent (vector DB), **LangGraph** (pipeline), OpenAI (`gpt-4o-mini`, `text-embedding-3-small`), **RapidOCR** (open-source OCR, no Tesseract binary), PyMuPDF (PDF text + page rendering), python-docx, pandas/openpyxl.

## Environment variables (`.env`, copy from `.env.example`)
`OPENAI_API_KEY` (required) · `OPENAI_MODEL` · `EMBED_MODEL` · `SECRET_KEY` (set a long random value) · `DATA_DIR` · `VISION_FALLBACK` (true = low-confidence/handwritten images are transcribed by OpenAI vision; false = fully local OCR) · `COOKIE_SECURE` (true behind HTTPS).

## Run locally (Python 3.10–3.12)
```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                  # put your OPENAI_API_KEY in it
python generate_samples.py                            # creates ./samples
python app.py                                         # http://localhost:5000
```
## Run with Docker (one command)
```bash
cp .env.example .env && docker compose up --build     # http://localhost:5000, data persisted in ./data
```
Production: gunicorn is already the container CMD. Keep **1 worker** (embedded Chroma/SQLite), put it behind HTTPS (Nginx/Caddy/Render/Railway/Fly with a volume on `/app/data`) and set `COOKIE_SECURE=true`.

## Try it
Sign up → upload everything in `samples/` → ask the questions in `samples/expected_results.json` (e.g. *Who was present on 2026-09-03?* → Asha Patil, Ravi Kumar, Priya Nair; *Who was present on 2026-12-25?* → not in data).

## API examples
```bash
curl -c j -H 'Content-Type: application/json' -d '{"email":"a@b.com","password":"password123"}' localhost:5000/api/signup
curl -b j -F files=@samples/attendance.csv -F files=@samples/attendance_scan.png localhost:5000/api/upload
curl -b j -H 'Content-Type: application/json' -d '{"question":"Average attendance from 2026-09-01 to 2026-09-03?"}' localhost:5000/api/chat
curl -b j -H 'Content-Type: application/json' -d '{"message_id":1,"rating":-1,"comment":"Answer in one line"}' localhost:5000/api/feedback
curl -b j -o att.xlsx 'localhost:5000/api/export?type=attendance&format=xlsx'     # type=chat|attendance, format=csv|xlsx
```
Endpoints: `POST /api/signup|login|logout`, `GET /api/me|files|history`, `POST /api/upload|chat|feedback`, `DELETE /api/files/<id>`, `GET /api/export`.

## Tests
`python -m pytest -q` (no OpenAI key or network needed: LLM + embeddings are stubbed; OCR runs for real).

| Mandatory scenario | Test |
|---|---|
| Who was present on a date + source evidence | `test_present_on_date_with_source_evidence` |
| Average attendance for a period | `test_average_attendance_and_highest` |
| Highest employee / department (ties) | `test_average_attendance_and_highest` |
| Answer not in data | `test_answer_not_in_data_says_so_with_no_sources` |
| All formats: CSV/XLSX/DOCX/PDF text, scanned PNG + PDF OCR | `test_sample_files_parse`, `test_ocr_scanned_image_and_pdf` |
| Unstructured text → canonical via LLM | `test_llm_fallback_normalises_unstructured_text` |
| Feedback injected into next prompt | `test_feedback_is_injected_into_next_prompt` |
| Security: auth, hashing, SQLi input, file type/path, isolation, CSV injection | `test_signup_login_logout_validation`, `test_auth_required`, `test_rejects_bad_extension_and_traversal_names`, `test_user_isolation`, `test_export_neutralises_formula_injection` |

Last run: **18 passed**.

## Known limitations
* **Handwriting**: RapidOCR is strong on printed scans but weak on real handwriting. With `VISION_FALLBACK=true` those images go to OpenAI vision (data leaves your machine). Generated "scans" are printed text, not real handwriting – test with your own handwritten sample.
* PDF text tables and OCR output are normalised by the LLM → can mis-read rows; the `ref` on each source lets you verify. Very large files = many LLM calls (cost/time); processing is synchronous.
* Identity = employee name (case-insensitive); two people with the same name merge. Ambiguous dates are read day-first (dd/mm).
* Statuses beyond Present/Absent/Leave (half-day, overtime, hours) are not modelled.
* No rate limiting / email verification / password reset; add a reverse-proxy limiter and HTTPS before public exposure. Single-process design (SQLite + embedded Chroma).
