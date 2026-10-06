import io, os, re, sys, tempfile
from pathlib import Path
os.environ.update(DATA_DIR=tempfile.mkdtemp(), OPENAI_API_KEY="test", VISION_FALLBACK="false", SECRET_KEY="t")
ROOT = Path(__file__).resolve().parent.parent; sys.path.insert(0, str(ROOT))
import pytest, core, app as appmod, generate_samples

CSV = b"""employee_id,employee_name,department,date,status
E1,Asha,Eng,2026-09-01,Present
E2,Ravi,Eng,2026-09-01,Absent
E3,Meera,HR,2026-09-01,Present
E1,Asha,Eng,2026-09-02,Present
E2,Ravi,Eng,2026-09-02,Leave
E3,Meera,HR,2026-09-02,Present
"""
SEEN = {}

def fake_llm(system, user, json_mode=True):
    SEEN["system"] = system
    if "query planner" in system:
        d = (re.search(r"\d{4}-\d{2}-\d{2}", user) or [None])[0]; u = user.lower()
        if "present" in u and d: return {"operation": "present_on_date", "date": d}
        if "average" in u: return {"operation": "avg_attendance"}
        if "highest department" in u: return {"operation": "top_department"}
        return {"operation": "other"}
    if "extract attendance" in system.lower():
        return {"records": [{"employee_name": "Zed Test", "department": "Ops", "date": "04/09/2026", "status": "Present"}]}
    return {"answer": user, "found": "COMPUTED RESULT" in user}  # "found" only when context carries a computed result

@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    monkeypatch.setattr(core, "llm", fake_llm)
    monkeypatch.setattr(core, "embed", lambda texts: [[1.0, 0.5, float(len(t) % 5), 0.1] for t in texts])

def user(email):
    c = appmod.app.test_client()
    assert c.post("/api/signup", json={"email": email, "password": "password123"}).status_code == 200
    return c

def up(c, data=CSV, name="att.csv"):
    return c.post("/api/upload", data={"files": (io.BytesIO(data), name)}, content_type="multipart/form-data").get_json()

def uid(email): return core.q("SELECT id FROM users WHERE email=?", (email,))[0]["id"]

# ---- auth & security
def test_signup_login_logout_validation():
    c = appmod.app.test_client()
    assert c.post("/api/signup", json={"email": "bad", "password": "password123"}).status_code == 400
    assert c.post("/api/signup", json={"email": "x@y.com", "password": "short"}).status_code == 400
    assert c.post("/api/signup", json={"email": "' OR 1=1 --", "password": "password123"}).status_code == 400
    assert c.post("/api/signup", json={"email": "a1@y.com", "password": "password123"}).status_code == 200
    assert c.post("/api/signup", json={"email": "a1@y.com", "password": "password123"}).status_code == 409
    c.post("/api/logout"); assert c.get("/api/me").status_code == 401
    assert c.post("/api/login", json={"email": "a1@y.com", "password": "nope"}).status_code == 401
    assert c.post("/api/login", json={"email": "a1@y.com", "password": "password123"}).status_code == 200
    assert "password123" not in core.q("SELECT pw FROM users WHERE email='a1@y.com'")[0]["pw"]  # hashed

@pytest.mark.parametrize("m,u", [("get", "/api/files"), ("post", "/api/chat"), ("post", "/api/upload"), ("get", "/api/export"), ("get", "/api/history"), ("post", "/api/feedback")])
def test_auth_required(m, u):
    assert getattr(appmod.app.test_client(), m)(u).status_code == 401

def test_rejects_bad_extension_and_traversal_names():
    c = user("sec@y.com")
    assert "error" in up(c, b"MZ", "evil.exe")[0]
    r = up(c, CSV, "../../etc/passwd.csv")[0]
    assert "error" not in r and ".." not in r["file"]

def test_user_isolation():
    a, b = user("iso_a@y.com"), user("iso_b@y.com")
    up(a)
    assert b.get("/api/files").get_json() == []
    mid = a.post("/api/chat", json={"question": "Who was present on 2026-09-01?"}).get_json()["id"]
    assert b.post("/api/feedback", json={"message_id": mid, "rating": 1}).status_code == 404
    assert b.post("/api/chat", json={"question": "Who was present on 2026-09-01?"}).get_json()["found"] is False
    assert core.compute(uid("iso_b@y.com"), {"operation": "avg_attendance"}) is None

def test_export_neutralises_formula_injection():
    c = user("csvinj@y.com")
    up(c, b"employee_name,date,status\n=HYPERLINK(\"http://x\"),2026-09-01,Present\n", "inj.csv")
    assert "'=HYPERLINK" in c.get("/api/export?type=attendance").get_data(as_text=True)

# ---- mandatory attendance scenarios
def test_present_on_date_with_source_evidence():
    c = user("q1@y.com"); assert up(c)[0]["records"] == 6
    r = c.post("/api/chat", json={"question": "Who was present on 2026-09-01?"}).get_json()
    assert r["found"] and "Asha" in r["answer"] and "Meera" in r["answer"] and "Ravi, " not in r["answer"].split("QUESTION")[0].split("COMPUTED RESULT")[1].split("EVIDENCE")[0]
    assert r["sources"] and r["sources"][0]["file"] == "att.csv" and "row" in r["sources"][0]["ref"]

def test_average_attendance_and_highest():
    c = user("q2@y.com"); up(c); u = uid("q2@y.com")
    assert "66.7%" in core.compute(u, {"operation": "avg_attendance"})["summary"]
    assert "0.0%" in core.compute(u, {"operation": "avg_attendance", "employee": "ravi"})["summary"]
    assert "Highest department attendance: HR at 100.0%" in core.compute(u, {"operation": "top_department"})["summary"]
    top = core.compute(u, {"operation": "top_employee"})["summary"]
    assert "Asha" in top and "Meera" in top  # tie reported, not hidden
    assert "Ravi" in core.compute(u, {"operation": "employee_status", "employee": "ravi", "date": "2026-09-02"})["summary"]

def test_answer_not_in_data_says_so_with_no_sources():
    c = user("q3@y.com"); up(c)
    r = c.post("/api/chat", json={"question": "Who was present on 2026-12-25?"}).get_json()
    assert r["found"] is False and r["sources"] == []
    assert "Upload" in user("q3b@y.com").post("/api/chat", json={"question": "hi"}).get_json()["answer"]

def test_llm_fallback_normalises_unstructured_text():
    c = user("q4@y.com")
    assert up(c, b"Zed was in the office on 4 Sep 2026", "note.txt")[0]["records"] == 1
    assert core.q("SELECT date FROM records WHERE user_id=?", (uid("q4@y.com"),))[0]["date"] == "2026-09-04"

# ---- feedback loop & export
def test_feedback_is_injected_into_next_prompt():
    c = user("fb@y.com"); up(c)
    mid = c.post("/api/chat", json={"question": "Who was present on 2026-09-01?"}).get_json()["id"]
    assert c.post("/api/feedback", json={"message_id": mid, "rating": -1, "comment": "Always answer in one short sentence"}).status_code == 200
    c.post("/api/chat", json={"question": "Who was present on 2026-09-02?"})
    assert "Always answer in one short sentence" in SEEN["system"]
    assert c.post("/api/feedback", json={"message_id": mid, "rating": 5}).status_code == 400

def test_export_formats():
    c = user("ex@y.com"); up(c); c.post("/api/chat", json={"question": "Who was present on 2026-09-01?"})
    assert c.get("/api/export?type=attendance").get_data(as_text=True).count("\n") == 7
    assert "att.csv" in c.get("/api/export?type=chat").get_data(as_text=True)
    assert c.get("/api/export?type=attendance&format=xlsx").data[:2] == b"PK"

# ---- sample files / parsers / OCR
def test_sample_files_parse(tmp_path):
    generate_samples.main(tmp_path)
    for f in ("attendance.csv", "attendance.xlsx", "attendance.docx"):
        assert len(core.parse(str(tmp_path / f), f)[1]) == 5, f
    segs, recs = core.parse(str(tmp_path / "attendance_text.pdf"), "t.pdf")
    assert not recs and "Asha Patil" in segs[0][1]

def test_ocr_scanned_image_and_pdf(tmp_path):
    pytest.importorskip("rapidocr_onnxruntime")
    generate_samples.main(tmp_path)
    for f in ("attendance_scan.png", "attendance_scan.pdf"):
        text = " ".join(t for _, t in core.parse(str(tmp_path / f), f)[0])
        assert "Asha" in text and "Meera" in text, f
