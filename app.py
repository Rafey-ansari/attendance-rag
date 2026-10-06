"""Flask API + static frontend."""
import io, json, os, re, uuid
from functools import wraps
from pathlib import Path

import pandas as pd
from flask import Flask, Response, jsonify, request, send_file, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

import core

app = Flask(__name__, static_folder="static", static_url_path="")
app.config.update(SECRET_KEY=os.getenv("SECRET_KEY", "dev-change-me"), MAX_CONTENT_LENGTH=25 * 1024 * 1024,
                  SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "false").lower() == "true")
core.init_db()

err = lambda msg, code=400: (jsonify(error=msg), code)

def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if "uid" not in session:
            return err("Login required", 401)
        return f(session["uid"], *a, **k)
    return w

@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")

# ---- auth
@app.post("/api/signup")
def signup():
    d = request.get_json(silent=True) or {}
    email, pw = str(d.get("email", "")).strip().lower(), str(d.get("password", ""))
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return err("Invalid email")
    if len(pw) < 8:
        return err("Password must be at least 8 characters")
    if core.q("SELECT 1 FROM users WHERE email=?", (email,)):
        return err("Email already registered", 409)
    session.clear()
    session["uid"] = core.q("INSERT INTO users(email,pw) VALUES (?,?)", (email, generate_password_hash(pw)))
    return jsonify(email=email)

@app.post("/api/login")
def login():
    d = request.get_json(silent=True) or {}
    u = core.q("SELECT * FROM users WHERE email=?", (str(d.get("email", "")).strip().lower(),))
    if not u or not check_password_hash(u[0]["pw"], str(d.get("password", ""))):
        return err("Wrong email or password", 401)
    session.clear(); session["uid"] = u[0]["id"]
    return jsonify(email=u[0]["email"])

@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)

@app.get("/api/me")
@login_required
def me(uid):
    return jsonify(email=core.q("SELECT email FROM users WHERE id=?", (uid,))[0]["email"])

# ---- files
@app.get("/api/files")
@login_required
def files(uid):
    return jsonify(core.q("SELECT id,name,n_records,created FROM files WHERE user_id=? ORDER BY id DESC", (uid,)))

@app.post("/api/upload")
@login_required
def upload(uid):
    res = []
    for f in request.files.getlist("files"):
        name = secure_filename(f.filename or "")
        if Path(name).suffix.lower() not in core.ALLOWED:
            res.append({"file": name or "?", "error": "Unsupported file type"}); continue
        folder = core.DATA / "uploads" / str(uid); folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{uuid.uuid4().hex}_{name}"
        f.save(path)
        try:
            res.append({"file": name, "records": core.ingest(uid, str(path), name)})
        except Exception as e:
            app.logger.exception("ingest failed")
            path.unlink(missing_ok=True)
            res.append({"file": name, "error": f"Processing failed ({type(e).__name__})"})
    return jsonify(res)

@app.delete("/api/files/<int:fid>")
@login_required
def del_file(uid, fid):
    return jsonify(ok=True) if core.delete_file(uid, fid) else err("Not found", 404)

# ---- chat + feedback
@app.post("/api/chat")
@login_required
def chat(uid):
    qn = str((request.get_json(silent=True) or {}).get("question", "")).strip()
    print(qn)
    if not qn or len(qn) > 1000:
    
        return err("Question must be 1-1000 characters")
    try:
        return jsonify(core.ask(uid, qn))
    except Exception:
        app.logger.exception("chat failed")
        return err("Could not answer right now (check OPENAI_API_KEY / logs)", 500)

@app.get("/api/history")
@login_required
def history(uid):
    rows = core.q("SELECT id,question,answer,sources,found FROM messages WHERE user_id=? ORDER BY id", (uid,))
    for r in rows:
        r["sources"] = json.loads(r["sources"] or "[]")
    return jsonify(rows)

@app.post("/api/feedback")
@login_required
def feedback(uid):
    d = request.get_json(silent=True) or {}
    if d.get("rating") not in (1, -1):
        return err("rating must be 1 or -1")
    if not core.q("SELECT 1 FROM messages WHERE id=? AND user_id=?", (d.get("message_id"), uid)):
        return err("Not found", 404)
    core.q("DELETE FROM feedback WHERE message_id=? AND user_id=?", (d["message_id"], uid))
    core.q("INSERT INTO feedback(user_id,message_id,rating,comment) VALUES (?,?,?,?)",
           (uid, d["message_id"], d["rating"], str(d.get("comment", ""))[:500]))
    return jsonify(ok=True)

# ---- export
def _safe(v):  # neutralise CSV/Excel formula injection
    return "'" + v if isinstance(v, str) and v and v[0] in "=+-@\t\r" else v

@app.get("/api/export")
@login_required
def export(uid):
    kind, fmt = request.args.get("type", "attendance"), request.args.get("format", "csv")
    if kind == "chat":
        rows = core.q("SELECT m.created,m.question,m.answer,m.sources,f.rating,f.comment FROM messages m "
                      "LEFT JOIN feedback f ON f.message_id=m.id WHERE m.user_id=? ORDER BY m.id", (uid,))
        for r in rows:
            r["sources"] = "; ".join(f"{s['file']} :: {s['ref']}" for s in json.loads(r["sources"] or "[]"))
        df = pd.DataFrame(rows, columns=["created", "question", "answer", "sources", "rating", "comment"])
    else:
        df = pd.DataFrame(core.q("SELECT employee_id,employee_name,department,date,status,file,ref FROM records WHERE user_id=? ORDER BY date,employee_name", (uid,)),
                          columns=["employee_id", "employee_name", "department", "date", "status", "file", "ref"])
    df = df.astype(object).apply(lambda c: c.map(_safe))
    if fmt == "xlsx":
        buf = io.BytesIO(); df.to_excel(buf, index=False); buf.seek(0)
        return send_file(buf, as_attachment=True, download_name=f"{kind}.xlsx",
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    return Response(df.to_csv(index=False), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename={kind}.csv"})

if __name__ == "__main__":
  
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
