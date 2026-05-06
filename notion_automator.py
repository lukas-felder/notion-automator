"""
notion_automator.py
====================
Automatisiert zwei Dinge in deiner Notion-Abmachungsdatenbank:

1. STATUS → "In Progress"
   Wenn der Erinnerungszeitpunkt (Reminder-Datum) erreicht ist,
   wechselt der Status automatisch auf "In Progress".
   → DeepFocus importiert den Task und blockiert Ablenkungsapps.

2. DATUM WIEDERHOLEN
   Wenn ein Task auf "Done" gesetzt wird, berechnet das Skript
   das nächste Fälligkeitsdatum gemäss dem definierten Rhythmus
   und setzt den Status zurück auf "Not started".

FELDER IN NOTION (müssen exakt so heissen):
--------------------------------------------
- "Status"              → Typ: Status  (Optionen: Not started, In Progress, Done)
- "Erinnerung"          → Typ: Date    (mit Uhrzeit aktivieren!)
- "Rhythmus"            → Typ: Select  (Optionen: täglich, wöchentlich,
                                         2-wöchentlich, monatlich, quartalsweise)
- "Nächste Fälligkeit"  → Typ: Date

CREDENTIALS (werden via GitHub Secrets übergeben, nie im Code speichern!):
---------------------------------------------------------------------------
NOTION_TOKEN  → Notion Integration Token (notion.so/my-integrations)
DATABASE_ID   → ID deiner Notion-Datenbank (aus der URL)
"""

import os
import requests
from datetime import datetime, timedelta, timezone
from dateutil.relativedelta import relativedelta

# ============================================================
# KONFIGURATION – Werte kommen aus GitHub Secrets (sicher!)
# ============================================================

NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
DATABASE_ID  = os.environ.get("DATABASE_ID")

if not NOTION_TOKEN or not DATABASE_ID:
    raise ValueError(
        "NOTION_TOKEN und DATABASE_ID müssen als Umgebungsvariablen gesetzt sein.\n"
        "Auf GitHub: Settings → Secrets → Actions → New repository secret"
    )

# Spaltennamen in Notion (gross/kleinschreibung beachten!)
COL_STATUS      = "Status"
COL_ERINNERUNG  = "Fälligkeit"
COL_RHYTHMUS    = "Rhythmus"
COL_FAELLIGKEIT = "Fälligkeit"

# Status-Werte (müssen exakt mit Notion übereinstimmen)
STATUS_OFFEN       = "Not started"
STATUS_IN_PROGRESS = "In Progress"
STATUS_DONE        = "Done"

# Toleranzfenster: Wie viele Minuten nach dem Erinnerungszeitpunkt
# darf das Skript noch reagieren? (GitHub Actions läuft alle 10 Min.)
TOLERANZ_MINUTEN = 12

# ============================================================
# NOTION API HILFSFUNKTIONEN
# ============================================================

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

def get_all_tasks():
    """Alle Einträge aus der Datenbank laden."""
    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
    response = requests.post(url, headers=HEADERS, json={"page_size": 100})
    response.raise_for_status()
    return response.json().get("results", [])

def update_task(page_id, properties):
    """Einen Eintrag in Notion aktualisieren."""
    url = f"https://api.notion.com/v1/pages/{page_id}"
    response = requests.patch(url, headers=HEADERS, json={"properties": properties})
    response.raise_for_status()
    return response.json()

def get_status(task):
    try:
        return task["properties"][COL_STATUS]["status"]["name"]
    except (KeyError, TypeError):
        return None

def get_erinnerung(task):
    """Erinnerungsdatum auslesen. Gibt timezone-aware datetime zurück."""
    try:
        date_str = task["properties"][COL_ERINNERUNG]["date"]["start"]
        if not date_str:
            return None
        # Notion gibt ISO-Format: "2025-05-09T16:00:00.000+02:00"
        # Python 3.11 kann das direkt parsen
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        # In UTC umwandeln für einfachen Vergleich
        return dt.astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None

def get_rhythmus(task):
    try:
        return task["properties"][COL_RHYTHMUS]["select"]["name"]
    except (KeyError, TypeError):
        return None

def get_name(task):
    try:
        return task["properties"]["Name"]["title"][0]["text"]["content"]
    except (KeyError, TypeError, IndexError):
        return "(Kein Name)"

# ============================================================
# KERNLOGIK
# ============================================================

def naechstes_datum(aktuelles_datum, rhythmus):
    """Nächstes Datum basierend auf Rhythmus berechnen."""
    r = rhythmus.lower().strip()
    if r == "täglich":
        return aktuelles_datum + timedelta(days=1)
    elif r == "wöchentlich":
        return aktuelles_datum + timedelta(weeks=1)
    elif r == "2-wöchentlich":
        return aktuelles_datum + timedelta(weeks=2)
    elif r == "monatlich":
        return aktuelles_datum + relativedelta(months=1)
    elif r == "quartalsweise":
        return aktuelles_datum + relativedelta(months=3)
    else:
        print(f"  ⚠ Unbekannter Rhythmus: '{rhythmus}'")
        return None

def check_und_aktiviere(task, jetzt_utc):
    """
    Setzt Status auf 'In Progress' wenn der Erinnerungszeitpunkt
    innerhalb des Toleranzfensters liegt.
    """
    if get_status(task) != STATUS_OFFEN:
        return

    erinnerung = get_erinnerung(task)
    if erinnerung is None:
        return

    name = get_name(task)
    delta_minuten = (jetzt_utc - erinnerung).total_seconds() / 60

    if 0 <= delta_minuten <= TOLERANZ_MINUTEN:
        print(f"  → Aktiviere: '{name}'")
        update_task(task["id"], {
            COL_STATUS: {"status": {"name": STATUS_IN_PROGRESS}}
        })
        print(f"    ✓ Status → In Progress")

def check_und_wiederhole(task):
    """
    Wenn ein Task 'Done' ist und einen Rhythmus hat:
    Nächstes Datum berechnen, Status zurücksetzen.
    """
    if get_status(task) != STATUS_DONE:
        return

    rhythmus = get_rhythmus(task)
    if rhythmus is None:
        return

    erinnerung = get_erinnerung(task)
    name = get_name(task)

    if erinnerung is None:
        print(f"  ⚠ '{name}': Done aber kein Erinnerungsdatum")
        return

    naechstes = naechstes_datum(erinnerung, rhythmus)
    if naechstes is None:
        return

    print(f"  → Wiederhole: '{name}' | {rhythmus}")
    print(f"    Nächstes Datum: {naechstes.strftime('%d.%m.%Y %H:%M')} UTC")

    update_task(task["id"], {
        COL_STATUS:      {"status": {"name": STATUS_OFFEN}},
        COL_ERINNERUNG:  {"date": {"start": naechstes.isoformat()}},
        COL_FAELLIGKEIT: {"date": {"start": naechstes.isoformat()}},
    })
    print(f"    ✓ Datum aktualisiert, Status → Not started")

# ============================================================
# HAUPTPROGRAMM
# ============================================================

def main():
    jetzt_utc = datetime.now(timezone.utc)
    print(f"\n{'='*50}")
    print(f"Notion Automator – {jetzt_utc.strftime('%d.%m.%Y %H:%M:%S')} UTC")
    print(f"{'='*50}")

    try:
        tasks = get_all_tasks()
        print(f"\n{len(tasks)} Einträge geladen.\n")
    except Exception as e:
        print(f"❌ Fehler beim Laden: {e}")
        raise

    print("── Schritt 1: Erinnerungen aktivieren ──")
    for task in tasks:
        try:
            check_und_aktiviere(task, jetzt_utc)
        except Exception as e:
            print(f"  ❌ Fehler bei '{get_name(task)}': {e}")

    print("\n── Schritt 2: Erledigte Tasks wiederholen ──")
    for task in tasks:
        try:
            check_und_wiederhole(task)
        except Exception as e:
            print(f"  ❌ Fehler bei '{get_name(task)}': {e}")

    print(f"\n✓ Fertig.\n")

if __name__ == "__main__":
    main()
