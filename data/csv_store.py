import csv, os
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(SCRIPT_DIR, "..", "sillage_log.csv")

FIELDS = [
    "id",
    "brand_name",
    "scent_name",
    "scent_medium",
    "scent_family",
    "scent_strength",
    "top_notes",
    "middle_notes",
    "base_notes",
    "predicted_rating",
    "actual_rating",
    "notes",
    "created_at",
]


def _resolve():
    return os.path.normpath(CSV_PATH)


def load_all():
    path = _resolve()
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_entry(entry):
    path = _resolve()
    existing = load_all()
    entry["id"] = len(existing) + 1
    entry["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    write_header = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerow(entry)


def update_actual_rating(entry_id, rating):
    path = _resolve()
    rows = load_all()
    for row in rows:
        if str(row.get("id")) == str(entry_id):
            row["actual_rating"] = rating
            break
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
