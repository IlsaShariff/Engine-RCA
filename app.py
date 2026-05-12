"""
Engine Tripping Root Cause Analysis System
Database-backed version (PostgreSQL on cloud, SQLite locally)
Run locally:  python app.py  →  http://localhost:5001
"""
import re, os, datetime
import pandas as pd
from flask import Flask, render_template, render_template_string, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)

# ── DATABASE CONFIG ──────────────────────────────────────────────────────────
# On Render: set DATABASE_URL environment variable to your PostgreSQL URL
# Locally:   uses SQLite (tripack_rca.db file, created automatically)
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///tripack_rca.db")
# Render gives postgres:// but SQLAlchemy needs postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)


# ── DATABASE MODEL ───────────────────────────────────────────────────────────
class TrippingLog(db.Model):
    __tablename__ = "tripping_log"
    id             = db.Column(db.Integer, primary_key=True)
    engine         = db.Column(db.String(50),  nullable=False)
    date           = db.Column(db.Date,         nullable=True)
    month          = db.Column(db.Integer,      nullable=True)
    year           = db.Column(db.Integer,      nullable=True)
    time           = db.Column(db.String(10),   nullable=True)
    running_hours  = db.Column(db.String(20),   nullable=True)
    details        = db.Column(db.Text,         nullable=True, default="")
    why1           = db.Column(db.Text,         nullable=True, default="")
    action_taken   = db.Column(db.Text,         nullable=True, default="")
    rca_category   = db.Column(db.String(100),  nullable=True, default="Other")
    created_at     = db.Column(db.DateTime,     default=datetime.datetime.utcnow)

    def to_dict(self):
        return {
            "id":            self.id,
            "engine":        self.engine,
            "date":          self.date,
            "month":         self.month,
            "year":          self.year,
            "time":          self.time or "",
            "running_hours": self.running_hours or "",
            "details":       self.details or "",
            "why1":          self.why1 or "",
            "action_taken":  self.action_taken or "",
            "rca_category":  self.rca_category or "Other",
        }


# ── CONSTANTS ────────────────────────────────────────────────────────────────
MONTH_NAMES = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

ENGINE_INFO = {
    "G1": "CAT 3520C", "G2": "CAT 3520C", "G3": "CAT 3520C",
    "G4": "CAT 3520C", "G5": "CAT 3520C", "G6": "CAT 3520C",
    "G7": "CAT 3520C", "G8": "CAT 3516C", "G9": "CAT 3520H",
    "N1": "Niigata #1", "N2": "Niigata #2",
    "Rental Eng.": "Rental Engine", "Blackout": "Complete Blackout",
}

RCA_CATEGORIES = {
    "Detonation / Knock":         ["detonation", "knock", "knocking"],
    "Spark Plug Failure":         ["spark plug", "ignition transformer"],
    "Fuel System Fault":          ["fuel valve", "fuel metering", "fuel differential",
                                   "gas solenoid", "shutoff valve", "fuel pressure",
                                   "fuel mertering"],
    "Sensor / Thermocouple Fault":["sensor", "thermocouple", "temperature sensor", "harness"],
    "Protection Relay Trip":      ["protection relay", "safety relay", "earth fault"],
    "Cylinder Deviation":         ["deviation high", "deviation low", "deviation alarm"],
    "CDVR / Voltage Issue":       ["cdvr", "voltage", "rectifier", "reverse power"],
    "Temperature High":           ["temperature high", "bearing temperature",
                                   "turbo.*temperature", "outlet temp"],
    "Lube Oil / Pressure":        ["lube oil", "oil pressure", "oil differential"],
    "Actuator / Throttle":        ["actuator", "throttle", "compressor bypass"],
    "Overspeed":                  ["overspeed"],
    "Load Variation":             ["load variation", "load drop", "load fluctuation"],
    "Emergency Stop":             ["emergency stop"],
    "GECM / ECM Fault":           ["gecm", "ecm"],
    "Blackout / Power Failure":   ["blackout", "dc breaker", "power supply", "master panel"],
    "UVT Malfunction":            ["uvt"],
    "External / Grid Fault":      ["enercon", "ke utility", "short circuit"],
}

ENGINES_3D = [
    {"id": "G1", "model": "G3520C", "serial": "GZN00574", "type": "Gas — 20 cyl",          "cls": "g-standard"},
    {"id": "G2", "model": "G3520C", "serial": "GZN00573", "type": "Gas — 20 cyl",          "cls": "g-standard"},
    {"id": "G3", "model": "G3520C", "serial": "GZN00585", "type": "Gas — 20 cyl",          "cls": "g-standard"},
    {"id": "G4", "model": "G3520C", "serial": "GZN00849", "type": "Gas — 20 cyl",          "cls": "g-standard"},
    {"id": "G5", "model": "G3520C", "serial": "GZN00850", "type": "Gas — 20 cyl",          "cls": "g-standard"},
    {"id": "G6", "model": "G3520C", "serial": "GZN00851", "type": "Gas — 20 cyl",          "cls": "g-standard"},
    {"id": "G7", "model": "G3520C", "serial": "GZN00867", "type": "Gas — 20 cyl",          "cls": "g-standard"},
    {"id": "G8", "model": "G3516C", "serial": "F6D00109", "type": "Diesel — 16 cyl",       "cls": "g-diesel"},
    {"id": "G9", "model": "G3520H", "serial": "GFP01349", "type": "Gas — High efficiency", "cls": "g-hi"},
]


# ── HELPERS ──────────────────────────────────────────────────────────────────
def classify_rca(text):
    if not isinstance(text, str):
        return "Other"
    t = text.lower()
    for cat, kws in RCA_CATEGORIES.items():
        if any(re.search(kw, t) for kw in kws):
            return cat
    return "Other"


def rows_to_df(rows):
    """Convert list of TrippingLog objects to a pandas DataFrame."""
    data = [r.to_dict() for r in rows]
    if not data:
        return pd.DataFrame(columns=["engine","date","month","year","time",
                                     "details","why1","action_taken","rca_category",
                                     "Engine","Date","Details","Why 1","Action Taken",
                                     "RCA_Category","Year","Month","Time"])
    df = pd.DataFrame(data)
    df["Date"]         = pd.to_datetime(df["date"], errors="coerce")
    df["Engine"]       = df["engine"].astype(str).str.strip()
    df["Details"]      = df["details"].fillna("").astype(str)
    df["Why 1"]        = df["why1"].fillna("").astype(str)
    df["Action Taken"] = df["action_taken"].fillna("").astype(str)
    df["RCA_Category"] = df["rca_category"].fillna("Other").astype(str)
    df["Year"]         = df["year"].fillna(2025).astype(int)
    df["Month"]        = df["month"].fillna(1).astype(int).clip(1, 12)
    df["Time"]         = df["time"].fillna("").astype(str)
    return df


def load_data():
    rows = TrippingLog.query.order_by(TrippingLog.date.desc()).all()
    return rows_to_df(rows)


def smart_rca(symptoms_text, engine_filter, df):
    sym = symptoms_text.lower()
    results = []
    for cat, kws in RCA_CATEGORIES.items():
        score = sum(1 for kw in kws if re.search(kw, sym))
        if score > 0:
            hist  = len(df[df["RCA_Category"] == cat])
            level = "High" if hist >= 3 else ("Medium" if hist >= 1 else "Low")
            results.append({
                "category":   cat,
                "score":      score * 2 + hist,
                "reason":     f"{score} keyword match(es). {hist} historical occurrence(s) in database.",
                "confidence": f"{level} ({score} keyword matches, {hist} past cases)"
            })
    results.sort(key=lambda x: x["score"], reverse=True)
    edf   = df[df["Engine"] == engine_filter].copy() if engine_filter else df.copy()
    words = set(re.findall(r'\w+', sym)) - {"the","a","and","on","in","of","to","for","is","was","with"}
    edf["_rel"] = edf.apply(
        lambda r: sum(1 for w in words if w in (r["Details"] + r["Why 1"]).lower()), axis=1)
    sim_rows = edf[edf["_rel"] > 0].sort_values("_rel", ascending=False).head(5)
    similar = [{"engine": r["Engine"],
                "date":   r["Date"].strftime("%d %b %Y") if pd.notna(r["Date"]) else "—",
                "rca":    r["RCA_Category"],
                "details":r["Details"][:300],
                "action": r["Action Taken"][:200]} for _, r in sim_rows.iterrows()]
    return results[:5], similar


def seed_from_excel():
    """Import data.xlsx into the database on first run (only if DB is empty)."""
    if TrippingLog.query.count() > 0:
        return  # already seeded
    xlsx_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.xlsx")
    if not os.path.exists(xlsx_path):
        return
    try:
        df = pd.read_excel(xlsx_path, sheet_name="Tripping Log", header=0)
        df = df.dropna(subset=["Engine"]).copy()
        df["Engine"] = df["Engine"].astype(str).str.strip()
        df["Engine"] = df["Engine"].apply(
            lambda x: re.sub(r"^g(\d)", lambda m: "G" + m.group(1), x))
        df["Details"]      = df.get("Details",      pd.Series(dtype=str)).fillna("").astype(str)
        df["Why 1"]        = df.get("Why 1",        pd.Series(dtype=str)).fillna("").astype(str)
        df["Action Taken"] = df.get("Action Taken", pd.Series(dtype=str)).fillna("").astype(str)
        df["RCA_Category"] = (df["Details"] + " " + df["Why 1"]).apply(classify_rca)
        df["Date"]         = pd.to_datetime(df.get("Date", pd.Series(dtype=str)), errors="coerce")

        def parse_year(v):
            if pd.isna(v): return None
            if hasattr(v, "year"): return v.year
            try: return int(float(v))
            except: return None

        yr_raw    = df.get("Year", pd.Series(dtype=object))
        yr_series = yr_raw.apply(parse_year)
        df["Year"]  = df["Date"].dt.year.combine_first(yr_series).fillna(2025).astype(int)
        df["Month"] = df["Date"].dt.month.fillna(
            df.get("Month", pd.Series(dtype=float))).fillna(1).astype(int).clip(1, 12)
        df["Time"]  = df.get("Time", pd.Series(dtype=str)).fillna("").astype(str)
        df["Running hours"] = df.get("Running hours", pd.Series(dtype=str)).fillna("").astype(str)

        for _, row in df.iterrows():
            entry = TrippingLog(
                engine        = str(row["Engine"]),
                date          = row["Date"].date() if pd.notna(row["Date"]) else None,
                month         = int(row["Month"]),
                year          = int(row["Year"]),
                time          = str(row["Time"])[:10],
                running_hours = str(row["Running hours"])[:20],
                details       = str(row["Details"]),
                why1          = str(row["Why 1"]),
                action_taken  = str(row["Action Taken"]),
                rca_category  = str(row["RCA_Category"]),
            )
            db.session.add(entry)
        db.session.commit()
        print(f"✅ Seeded {len(df)} records from data.xlsx into database.")
    except Exception as e:
        print(f"⚠️  Excel seed error: {e}")
        db.session.rollback()


# ── ROUTES ───────────────────────────────────────────────────────────────────
@app.route("/")
def dashboard():
    df = load_data()
    years        = sorted(df["Year"].unique().tolist())
    total_trips  = len(df)
    if total_trips == 0:
        return render_template("dashboard.html", page="dashboard",
            total_trips=0, years=[], top_engine="—", top_engine_trips=0,
            engine_count=0, top_rca="—", top_rca_pct=0,
            yearly_counts=[], yearly_table=[], months=list(MONTH_NAMES[1:]),
            monthly_table=[], monthly_datasets=[], engine_labels=[],
            engine_values=[], engine_table=[], rca_sorted=[],
            rca_labels=[], rca_values=[])

    ec             = df["Engine"].value_counts()
    top_engine     = ec.index[0];  top_engine_trips = int(ec.iloc[0])
    engine_count   = df["Engine"].nunique()
    rc             = df["RCA_Category"].value_counts()
    top_rca        = rc.index[0];  top_rca_pct = round(rc.iloc[0] / total_trips * 100, 1)

    yearly_counts, yearly_table = [], []
    for y in years:
        ydf = df[df["Year"] == y]
        cnt = len(ydf);  yearly_counts.append(cnt)
        tc  = ydf["RCA_Category"].value_counts()
        yearly_table.append({"year": y, "trips": cnt,
                              "engines": ydf["Engine"].nunique(),
                              "top_cause": tc.index[0] if len(tc) else "—"})

    months = [MONTH_NAMES[i] for i in range(1, 13)]
    colors = ["#4f8ef7", "#22c55e", "#f59e0b", "#ef4444", "#7c3aed"]
    monthly_table, monthly_datasets = [], []
    for i, y in enumerate(years):
        ydf  = df[df["Year"] == y]
        vals = [int(len(ydf[ydf["Month"] == m])) for m in range(1, 13)]
        monthly_table.append({"year": y, "counts": vals, "total": sum(vals)})
        monthly_datasets.append({"label": str(y), "data": vals,
            "borderColor": colors[i % len(colors)],
            "backgroundColor": colors[i % len(colors)] + "33",
            "tension": 0.4, "fill": False})

    all_eng      = sorted(df["Engine"].unique().tolist())
    engine_labels = all_eng
    engine_values = [int(ec.get(e, 0)) for e in all_eng]
    engine_table  = []
    for e in all_eng:
        edf  = df[df["Engine"] == e]
        tc2  = edf["RCA_Category"].value_counts()
        engine_table.append({"engine": e, "type": ENGINE_INFO.get(e, "Engine"),
                              "total": len(edf),
                              "by_year": [int(len(edf[edf["Year"] == y])) for y in years],
                              "top_cause": tc2.index[0] if len(tc2) else "—"})
    rca_sorted = list(rc.items())
    return render_template("dashboard.html", page="dashboard",
        total_trips=total_trips, years=years, top_engine=top_engine,
        top_engine_trips=top_engine_trips, engine_count=engine_count,
        top_rca=top_rca, top_rca_pct=top_rca_pct,
        yearly_counts=yearly_counts, yearly_table=yearly_table,
        months=months, monthly_table=monthly_table, monthly_datasets=monthly_datasets,
        engine_labels=engine_labels, engine_values=engine_values,
        engine_table=engine_table, rca_sorted=rca_sorted,
        rca_labels=[r[0] for r in rca_sorted], rca_values=[r[1] for r in rca_sorted])


@app.route("/explorer")
def explorer():
    df = load_data()
    ec = df["Engine"].value_counts()
    all_engines = [{"name": e, "type": ENGINE_INFO.get(e, "Engine"), "trips": int(ec.get(e, 0))}
                   for e in sorted(df["Engine"].unique())]
    selected = request.args.get("engine", "")
    records, rca_breakdown, rca_labels, rca_vals = [], [], [], []
    first_date = last_date = top_cause = eng_type = "—"
    if selected:
        edf = df[df["Engine"] == selected].sort_values("Date", ascending=False)
        eng_type = ENGINE_INFO.get(selected, "Unknown")
        for _, r in edf.iterrows():
            records.append({
                "date":    r["Date"].strftime("%d %b %Y") if pd.notna(r["Date"]) else "—",
                "time":    str(r["Time"])[:5],
                "rca":     r["RCA_Category"],
                "details": r["Details"],
                "action":  r["Action Taken"]})
        rc2 = edf["RCA_Category"].value_counts()
        n   = max(len(edf), 1)
        for cat, cnt in rc2.items():
            rca_breakdown.append({"cat": cat, "count": int(cnt), "pct": round(cnt / n * 100, 1)})
            rca_labels.append(cat); rca_vals.append(int(cnt))
        top_cause = rc2.index[0] if len(rc2) else "—"
        dates = edf["Date"].dropna()
        if len(dates):
            first_date = dates.min().strftime("%d %b %Y")
            last_date  = dates.max().strftime("%d %b %Y")
    return render_template("explorer.html", page="explorer",
        all_engines=all_engines, selected_engine=selected,
        records=records, rca_breakdown=rca_breakdown,
        rca_labels=rca_labels, rca_vals=rca_vals,
        first_date=first_date, last_date=last_date,
        top_cause=top_cause, eng_type=eng_type)


@app.route("/rca", methods=["GET", "POST"])
def rca_page():
    df = load_data()
    all_engines = sorted(df["Engine"].unique().tolist())
    sel_engine = sel_symptoms = confirm_msg = ""
    results = []; similar = []
    if request.method == "POST":
        sel_engine   = request.form.get("engine", "")
        sel_symptoms = request.form.get("symptoms", "")
        if sel_symptoms.strip():
            results, similar = smart_rca(sel_symptoms, sel_engine, df)
    return render_template("rca.html", page="rca", all_engines=all_engines,
        sel_engine=sel_engine, sel_symptoms=sel_symptoms,
        results=results, similar=similar, confirm_msg=confirm_msg)


@app.route("/rca/confirm", methods=["POST"])
def rca_confirm():
    engine        = request.form.get("engine", "")
    symptoms      = request.form.get("symptoms", "")
    confirmed_rca = request.form.get("confirmed_rca", "")
    action        = request.form.get("action", "")
    confirm_msg   = ""
    try:
        now   = datetime.datetime.now()
        entry = TrippingLog(
            engine       = engine or "Unknown",
            date         = now.date(),
            month        = now.month,
            year         = now.year,
            time         = now.strftime("%H:%M"),
            details      = f"[Confirmed] Symptoms: {symptoms}. Cause: {confirmed_rca}",
            action_taken = action,
            rca_category = confirmed_rca or classify_rca(symptoms),
        )
        db.session.add(entry)
        db.session.commit()
        confirm_msg = f"✅ RCA '{confirmed_rca}' saved for {engine}."
    except Exception as e:
        db.session.rollback()
        confirm_msg = f"⚠️ Error saving: {e}"
    df2 = load_data()
    all_engines = sorted(df2["Engine"].unique().tolist())
    results, similar = smart_rca(symptoms, engine, df2) if symptoms else ([], [])
    return render_template("rca.html", page="rca", all_engines=all_engines,
        sel_engine=engine, sel_symptoms=symptoms,
        results=results, similar=similar, confirm_msg=confirm_msg)


@app.route("/update")
def update_page():
    df = load_data()
    all_engines = sorted(df["Engine"].unique().tolist())
    recent = [{"engine": r["Engine"],
               "date":   r["Date"].strftime("%d %b %Y") if pd.notna(r["Date"]) else "—",
               "rca":    r["RCA_Category"],
               "details":r["Details"]} for _, r in df.head(10).iterrows()]
    return render_template("update.html", page="update", all_engines=all_engines,
        recent=recent, today=datetime.date.today().isoformat(),
        add_msg="", add_ok=True, upload_msg="", upload_ok=True)


@app.route("/update/add", methods=["POST"])
def update_add():
    add_msg = ""; add_ok = True
    try:
        date_val     = pd.to_datetime(request.form.get("date", ""))
        details      = request.form.get("details", "")
        action       = request.form.get("action", "")
        rca_cat      = classify_rca(details)
        entry = TrippingLog(
            engine        = request.form.get("engine", ""),
            date          = date_val.date(),
            month         = date_val.month,
            year          = date_val.year,
            time          = request.form.get("time", ""),
            running_hours = request.form.get("running_hours", ""),
            details       = details,
            action_taken  = action,
            rca_category  = rca_cat,
        )
        db.session.add(entry)
        db.session.commit()
        add_msg = f"✅ Entry added for {entry.engine} on {request.form.get('date')} — classified as '{rca_cat}'."
    except Exception as e:
        db.session.rollback()
        add_msg = f"❌ Error: {e}"; add_ok = False
    df = load_data()
    all_engines = sorted(df["Engine"].unique().tolist())
    recent = [{"engine": r["Engine"],
               "date":   r["Date"].strftime("%d %b %Y") if pd.notna(r["Date"]) else "—",
               "rca":    r["RCA_Category"],
               "details":r["Details"]} for _, r in df.head(10).iterrows()]
    return render_template("update.html", page="update", all_engines=all_engines,
        recent=recent, today=datetime.date.today().isoformat(),
        add_msg=add_msg, add_ok=add_ok, upload_msg="", upload_ok=True)


@app.route("/update/upload", methods=["POST"])
def update_upload():
    upload_msg = ""; upload_ok = True
    f = request.files.get("file")
    if f and f.filename.lower().endswith((".xlsx", ".xls")):
        try:
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
                f.save(tmp.name)
                df_new = pd.read_excel(tmp.name, sheet_name="Tripping Log", header=0)
            df_new = df_new.dropna(subset=["Engine"]).copy()
            df_new["Engine"] = df_new["Engine"].astype(str).str.strip()
            df_new["Engine"] = df_new["Engine"].apply(
                lambda x: re.sub(r"^g(\d)", lambda m: "G" + m.group(1), x))
            df_new["Details"]      = df_new.get("Details",      pd.Series(dtype=str)).fillna("").astype(str)
            df_new["Why 1"]        = df_new.get("Why 1",        pd.Series(dtype=str)).fillna("").astype(str)
            df_new["Action Taken"] = df_new.get("Action Taken", pd.Series(dtype=str)).fillna("").astype(str)
            df_new["RCA_Category"] = (df_new["Details"] + " " + df_new["Why 1"]).apply(classify_rca)
            df_new["Date"]         = pd.to_datetime(df_new.get("Date", pd.Series(dtype=str)), errors="coerce")

            def parse_year(v):
                if pd.isna(v): return None
                if hasattr(v, "year"): return v.year
                try: return int(float(v))
                except: return None

            yr_series = df_new.get("Year", pd.Series(dtype=object)).apply(parse_year)
            df_new["Year"]  = df_new["Date"].dt.year.combine_first(yr_series).fillna(2025).astype(int)
            df_new["Month"] = df_new["Date"].dt.month.fillna(
                df_new.get("Month", pd.Series(dtype=float))).fillna(1).astype(int).clip(1, 12)
            df_new["Time"]  = df_new.get("Time", pd.Series(dtype=str)).fillna("").astype(str)
            df_new["Running hours"] = df_new.get("Running hours", pd.Series(dtype=str)).fillna("").astype(str)

            TrippingLog.query.delete()
            for _, row in df_new.iterrows():
                entry = TrippingLog(
                    engine        = str(row["Engine"]),
                    date          = row["Date"].date() if pd.notna(row["Date"]) else None,
                    month         = int(row["Month"]),
                    year          = int(row["Year"]),
                    time          = str(row["Time"])[:10],
                    running_hours = str(row["Running hours"])[:20],
                    details       = str(row["Details"]),
                    why1          = str(row["Why 1"]),
                    action_taken  = str(row["Action Taken"]),
                    rca_category  = str(row["RCA_Category"]),
                )
                db.session.add(entry)
            db.session.commit()
            upload_msg = f"✅ File uploaded! {len(df_new)} records imported into database successfully."
        except Exception as e:
            db.session.rollback()
            upload_msg = f"❌ Error reading file: {e}"; upload_ok = False
    else:
        upload_msg = "❌ Please select a valid .xlsx file."; upload_ok = False

    df2 = load_data()
    all_engines = sorted(df2["Engine"].unique().tolist())
    recent = [{"engine": r["Engine"],
               "date":   r["Date"].strftime("%d %b %Y") if pd.notna(r["Date"]) else "—",
               "rca":    r["RCA_Category"],
               "details":r["Details"]} for _, r in df2.head(10).iterrows()]
    return render_template("update.html", page="update", all_engines=all_engines,
        recent=recent, today=datetime.date.today().isoformat(),
        add_msg="", add_ok=True, upload_msg=upload_msg, upload_ok=upload_ok)


# ── 3D MODELS PAGE ───────────────────────────────────────────────────────────
MODELS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>3D Engine Models — Engine RCA System</title>
<style>
  :root{--bg:#0f1117;--surface:#1a1d27;--surface2:#222538;--border:#2e3250;
    --accent:#4f8ef7;--green:#22c55e;--blue:#378ADD;--yellow:#f59e0b;
    --text:#e2e8f0;--muted:#8892aa;--radius:12px;}
  *{box-sizing:border-box;margin:0;padding:0;}
  body{font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;}
  nav{background:var(--surface);border-bottom:1px solid var(--border);padding:0 2rem;
      display:flex;align-items:center;gap:0;position:sticky;top:0;z-index:100;}
  .logo{font-size:1.1rem;font-weight:700;color:var(--accent);padding:1rem 1.5rem 1rem 0;
        border-right:1px solid var(--border);margin-right:1rem;letter-spacing:.5px;}
  .logo span{color:#fff;}
  nav a{color:var(--muted);text-decoration:none;padding:.9rem 1.1rem;font-size:.9rem;
        border-bottom:3px solid transparent;transition:all .2s;}
  nav a:hover{color:var(--text);}
  nav a.active{color:var(--accent);border-bottom-color:var(--accent);}
  .container{max-width:1400px;margin:0 auto;padding:2rem;}
  .page-header{margin-bottom:1.5rem;}
  .page-header h1{font-size:1.8rem;font-weight:700;}
  .page-header p{color:var(--muted);margin-top:.4rem;}
  .legend{display:flex;gap:1.5rem;margin-bottom:1.5rem;flex-wrap:wrap;}
  .leg-item{display:flex;align-items:center;gap:6px;font-size:.78rem;color:var(--muted);}
  .leg-dot{width:10px;height:10px;border-radius:2px;}
  .fleet-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));
              gap:1px;background:var(--border);border:1px solid var(--border);
              border-radius:var(--radius);overflow:hidden;margin-bottom:2rem;}
  .eng-card{background:var(--surface);padding:1.4rem 1.5rem;cursor:pointer;
            position:relative;transition:background .18s;overflow:hidden;}
  .eng-card::before{content:'';position:absolute;top:0;left:0;width:3px;height:100%;
    background:var(--accent);transform:scaleY(0);transform-origin:bottom;
    transition:transform .22s cubic-bezier(.4,0,.2,1);}
  .eng-card.g-diesel::before{background:var(--blue);}
  .eng-card.g-hi::before{background:var(--green);}
  .eng-card:hover{background:var(--surface2);}
  .eng-card:hover::before{transform:scaleY(1);}
  .card-top{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px;}
  .gen-id{font-size:2rem;font-weight:700;line-height:1;color:var(--accent);}
  .eng-card.g-diesel .gen-id{color:var(--blue);}
  .eng-card.g-hi .gen-id{color:var(--green);}
  .status-pip{width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 6px #22c55e88;margin-top:6px;}
  .model-lbl{font-size:.72rem;font-weight:700;letter-spacing:1.5px;color:var(--muted);text-transform:uppercase;margin-bottom:4px;}
  .serial-lbl{font-size:.78rem;color:var(--muted);margin-bottom:14px;}
  .serial-lbl span{color:var(--text);font-weight:500;}
  .card-footer{display:flex;align-items:center;justify-content:space-between;}
  .eng-type-txt{font-size:.72rem;color:var(--muted);}
  .view-3d-btn{font-size:.72rem;font-weight:700;letter-spacing:1px;color:var(--accent);
    text-transform:uppercase;display:flex;align-items:center;gap:4px;
    opacity:0;transform:translateX(-6px);transition:opacity .18s,transform .18s;}
  .eng-card.g-diesel .view-3d-btn{color:var(--blue);}
  .eng-card.g-hi .view-3d-btn{color:var(--green);}
  .eng-card:hover .view-3d-btn{opacity:1;transform:translateX(0);}
  .panel{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:1.5rem;}
  .panel h3{font-size:1rem;font-weight:600;margin-bottom:1rem;}
  .how-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:1rem;}
  .how-card{background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:1rem;}
  .how-label{font-size:.72rem;color:var(--muted);text-transform:uppercase;letter-spacing:.8px;margin-bottom:.4rem;}
  .how-title{font-size:.88rem;font-weight:600;color:var(--text);margin-bottom:.3rem;}
  .how-sub{font-size:.78rem;color:var(--muted);}
  .modal-bg{display:none;position:fixed;inset:0;z-index:200;background:rgba(0,0,0,.75);align-items:center;justify-content:center;}
  .modal-bg.open{display:flex;}
  .modal-box{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);width:480px;max-width:95vw;overflow:hidden;animation:popIn .2s ease;}
  @keyframes popIn{from{opacity:0;transform:scale(.95) translateY(8px);}to{opacity:1;transform:scale(1) translateY(0);}}
  .modal-head{background:var(--surface2);border-bottom:1px solid var(--border);padding:1rem 1.25rem;display:flex;align-items:center;gap:12px;}
  .modal-gen-id{font-size:1.5rem;font-weight:700;color:var(--accent);}
  .modal-gen-id.diesel{color:var(--blue);}
  .modal-gen-id.hi{color:var(--green);}
  .modal-head-info{flex:1;}
  .modal-model-lbl{font-size:.8rem;font-weight:600;letter-spacing:1px;color:var(--muted);text-transform:uppercase;}
  .modal-serial-txt{font-size:.78rem;color:var(--muted);margin-top:2px;}
  .modal-serial-txt span{color:var(--text);font-weight:500;}
  .modal-close{background:none;border:1px solid var(--border);color:var(--muted);width:28px;height:28px;border-radius:6px;cursor:pointer;font-size:1rem;display:flex;align-items:center;justify-content:center;transition:border-color .15s,color .15s;}
  .modal-close:hover{border-color:var(--accent);color:var(--accent);}
  .modal-body{padding:1.5rem 1.25rem;}
  .sys-tags{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:1.25rem;}
  .sys-tag{font-size:.7rem;letter-spacing:.5px;text-transform:uppercase;padding:3px 8px;border-radius:4px;background:var(--surface2);border:1px solid var(--border);color:var(--muted);}
  .open-sis-btn{display:flex;align-items:center;justify-content:center;gap:8px;width:100%;padding:.85rem;background:var(--accent);color:#fff;border:none;border-radius:8px;font-size:.9rem;font-weight:700;cursor:pointer;text-decoration:none;transition:opacity .15s;}
  .open-sis-btn.diesel{background:var(--blue);}
  .open-sis-btn.hi{background:var(--green);}
  .open-sis-btn:hover{opacity:.88;}
  .modal-note{font-size:.75rem;color:var(--muted);text-align:center;margin-top:.85rem;line-height:1.6;}
</style>
</head>
<body>
<nav>
  <div class="logo">⚡ <span>Engine RCA</span></div>
  <a href="/">📊 Dashboard</a>
  <a href="/explorer">🔍 Engine History</a>
  <a href="/rca">🤖 Smart RCA</a>
  <a href="/update">➕ Add Data</a>
  <a href="/models" class="active">🔩 3D Models</a>
</nav>
<div class="container">
  <div class="page-header">
    <h1>🔩 Engine 3D Models</h1>
    <p>Select any generator to open its live interactive 3D model on CAT SIS 2.0</p>
  </div>
  <div class="legend">
    <div class="leg-item"><div class="leg-dot" style="background:var(--accent)"></div>G3520C — Gas 20-cylinder</div>
    <div class="leg-item"><div class="leg-dot" style="background:var(--blue)"></div>G3516C — Diesel 16-cylinder</div>
    <div class="leg-item"><div class="leg-dot" style="background:var(--green)"></div>G3520H — High efficiency gas</div>
  </div>
  <div class="fleet-grid">
    {% for e in engines %}
    <div class="eng-card {{ e.cls }}" onclick="openModal('{{ e.id }}','{{ e.model }}','{{ e.serial }}','{{ e.type }}','{{ e.cls }}')">
      <div class="card-top"><div class="gen-id">{{ e.id }}</div><div class="status-pip"></div></div>
      <div class="model-lbl">{{ e.model }}</div>
      <div class="serial-lbl">Serial: <span>{{ e.serial }}</span></div>
      <div class="card-footer">
        <div class="eng-type-txt">{{ e.type }}</div>
        <div class="view-3d-btn">View 3D <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M2 6h8M6 2l4 4-4 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg></div>
      </div>
    </div>
    {% endfor %}
  </div>
  <div class="panel">
    <h3>How to use the 3D viewer</h3>
    <div class="how-grid">
      <div class="how-card"><div class="how-label">Step 1</div><div class="how-title">Click any engine card</div><div class="how-sub">A popup shows engine details and available systems</div></div>
      <div class="how-card"><div class="how-label">Step 2</div><div class="how-title">Click "Open 3D Model"</div><div class="how-sub">Opens CAT SIS 2.0 directly to that engine's 3D tab</div></div>
      <div class="how-card"><div class="how-label">Step 3</div><div class="how-title">Explore in SIS 2.0</div><div class="how-sub">Rotate, zoom, toggle systems, click parts for details</div></div>
      <div class="how-card"><div class="how-label">Systems available</div><div class="how-title">All major assemblies</div><div class="how-sub">Basic engine, cooling, fuel, electrical, air inlet &amp; exhaust</div></div>
    </div>
  </div>
</div>
<div class="modal-bg" id="modal-bg" onclick="closeModalBg(event)">
  <div class="modal-box">
    <div class="modal-head">
      <div class="modal-gen-id" id="m-id">G1</div>
      <div class="modal-head-info">
        <div class="modal-model-lbl" id="m-model">G3520C</div>
        <div class="modal-serial-txt">Serial: <span id="m-serial">GZN00574</span></div>
      </div>
      <button class="modal-close" onclick="closeModal()">×</button>
    </div>
    <div class="modal-body">
      <div class="sys-tags">
        <span class="sys-tag">Basic Engine</span><span class="sys-tag">Air Inlet &amp; Exhaust</span>
        <span class="sys-tag">Cooling System</span><span class="sys-tag">Electrical</span>
        <span class="sys-tag">Lubrication</span><span class="sys-tag">Fuel System</span>
        <span class="sys-tag">Engine Arrangement</span>
      </div>
      <a id="m-link" href="#" target="_blank" class="open-sis-btn">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M3 8h10M9 4l4 4-4 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
        Open 3D Model in CAT SIS 2.0
      </a>
      <div class="modal-note">Opens the interactive CAT SIS 2.0 3D viewer for this engine's serial number.<br>Requires an active CAT SIS 2.0 account.</div>
    </div>
  </div>
</div>
<script>
function openModal(id,model,serial,type,cls){
  document.getElementById('m-id').textContent=id;
  document.getElementById('m-id').className='modal-gen-id'+(cls==='g-diesel'?' diesel':cls==='g-hi'?' hi':'');
  document.getElementById('m-model').textContent=model;
  document.getElementById('m-serial').textContent=serial;
  var btn=document.getElementById('m-link');
  btn.href='https://sis2.cat.com/#/detail?serialNumber='+serial+'&tab=3D';
  btn.className='open-sis-btn'+(cls==='g-diesel'?' diesel':cls==='g-hi'?' hi':'');
  document.getElementById('modal-bg').classList.add('open');
}
function closeModal(){document.getElementById('modal-bg').classList.remove('open');}
function closeModalBg(e){if(e.target===document.getElementById('modal-bg'))closeModal();}
document.addEventListener('keydown',function(e){if(e.key==='Escape')closeModal();});
</script>
</body>
</html>"""


@app.route("/models")
def models_page():
    return render_template_string(MODELS_HTML, engines=ENGINES_3D)


# ── STARTUP ──────────────────────────────────────────────────────────────────
with app.app_context():
    db.create_all()
    seed_from_excel()

if __name__ == "__main__":
    print("\n" + "="*55)
    print("  ⚡  ENGINE TRIPPING RCA SYSTEM  (DB Edition)")
    print("  Open your browser → http://localhost:5001")
    print("="*55 + "\n")
    app.run(debug=False, host="0.0.0.0", port=5001)
