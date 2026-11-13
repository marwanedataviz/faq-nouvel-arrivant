"""Copie les données du Grist « [PEC] Structure du kit du nouvel arrivant »
vers data.json, le fichier lu par la page FAQ.

Lancé chaque nuit par GitHub (.github/workflows/update.yml).
La clé API Grist est lue dans la variable d'environnement GRIST_API_KEY
(rangée dans les « secrets » GitHub, jamais écrite dans le code).

Seules les informations utiles à la FAQ sont copiées :
question, moment du parcours, sujet, interlocuteurs, ressources (titre,
description, type, lien). Les questions « Pour les managers » sont exclues.
"""

import json
import os
import sys
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = os.environ.get("GRIST_BASE", "https://grist.numerique.gouv.fr/api").rstrip("/")
DOC = os.environ.get("GRIST_DOC_ID", "1kLpyXStcben")
KEY = os.environ.get("GRIST_API_KEY", "")
OUT = os.environ.get("OUTPUT", "data.json")

T_QUESTIONS = "Unites_editoriales"
T_SOURCES = "Sources"
T_THEMES = "Thematique"
T_CONTACTS = "Contributeurs"

STEP_ORDER = ["Premier jour", "Première semaine", "Premier mois", "Première année"]


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return " ".join(s.lower().replace("_", " ").split())


def get(path, params=None):
    url = f"{BASE}/docs/{DOC}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {KEY}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def columns(table):
    return get(f"tables/{table}/columns")["columns"]


def records(table):
    try:
        return get(f"tables/{table}/records", {"sort": "manualSort"})["records"]
    except Exception:
        return get(f"tables/{table}/records")["records"]


def find_col(cols, *labels, required=True):
    """Retrouve l'identifiant d'une colonne à partir de son libellé visible."""
    wanted = [norm(l) for l in labels]
    for c in cols:
        if norm(c["fields"].get("label")) in wanted or norm(c["id"]) in wanted:
            return c["id"]
    if required:
        names = ", ".join(c["fields"].get("label") or c["id"] for c in cols)
        sys.exit(f"Colonne introuvable ({' / '.join(labels)}). Colonnes présentes : {names}")
    return None


def ref_ids(value):
    """Valeur d'une colonne Référence (int) ou Liste de références (["L", ...])."""
    if value in (None, 0, ""):
        return []
    if isinstance(value, int):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, int)]
    return []


def clean_url(value):
    v = str(value or "").strip()
    if not v:
        return ""
    # Format lien Grist possible : « texte https://... »
    last = v.split()[-1]
    return last if last.startswith(("http://", "https://")) else (v if v.startswith("http") else "")


def text(value):
    if value is None:
        return ""
    if isinstance(value, list):  # Choice list
        return ", ".join(str(v) for v in value[1:] if v)
    return str(value).strip()


def main():
    if not KEY:
        sys.exit("GRIST_API_KEY manquante.")

    # --- Sujets
    th_cols = columns(T_THEMES)
    th_name = find_col(th_cols, "theme_name", "Thématique", "Nom")
    themes = {r["id"]: text(r["fields"].get(th_name)) for r in records(T_THEMES)}

    # --- Interlocuteurs
    contacts = {}
    try:
        ct_cols = columns(T_CONTACTS)
        ct_name = find_col(ct_cols, "Interlocuteurs", "Interlocuteur", "Nom")
        contacts = {r["id"]: text(r["fields"].get(ct_name)) for r in records(T_CONTACTS)}
    except Exception as e:  # non bloquant : la colonne est encore vide
        print(f"Interlocuteurs ignorés : {e}")

    # --- Ressources
    so_cols = columns(T_SOURCES)
    so_title = find_col(so_cols, "Titre")
    so_desc = find_col(so_cols, "Description", required=False)
    so_type = find_col(so_cols, "Type", required=False)
    so_url = find_col(so_cols, "Lien", "Liens", "URL", "Url", "Adresse", required=False)
    so_recs = records(T_SOURCES)
    if not so_url:  # pas de libellé reconnu : on prend la colonne qui contient des liens
        for c in so_cols:
            vals = [str(r["fields"].get(c["id"]) or "") for r in so_recs]
            if sum("http" in v for v in vals) >= max(1, len(vals) // 2):
                so_url = c["id"]
                break
    sources = {}
    for r in so_recs:
        f = r["fields"]
        sources[r["id"]] = {
            "titre": text(f.get(so_title)),
            "description": text(f.get(so_desc)) if so_desc else "",
            "type": text(f.get(so_type)) if so_type else "",
            "url": clean_url(f.get(so_url)) if so_url else "",
        }

    # --- Questions
    q_cols = columns(T_QUESTIONS)
    q_title = find_col(q_cols, "Titre du message", "Titre")
    q_theme = find_col(q_cols, "Thématique", "Thematique", required=False)
    q_step = find_col(q_cols, "Étape du parcours", "Etape du parcours", "Etape")
    q_contacts = find_col(q_cols, "Interlocuteurs", required=False)
    q_sources = find_col(q_cols, "Sources", required=False)

    questions = []
    for r in records(T_QUESTIONS):
        f = r["fields"]
        titre = text(f.get(q_title))
        etape = text(f.get(q_step))
        if not titre:
            continue
        if "manager" in norm(etape):
            continue
        questions.append({
            "titre": titre,
            "etape": etape or "Autres",
            "theme": ", ".join(themes.get(i, "") for i in ref_ids(f.get(q_theme)) if themes.get(i)) if q_theme else "",
            "interlocuteurs": [contacts[i] for i in ref_ids(f.get(q_contacts)) if contacts.get(i)] if q_contacts else [],
            "ressources": [sources[i] for i in ref_ids(f.get(q_sources)) if i in sources and sources[i]["titre"]] if q_sources else [],
        })

    if not questions:
        sys.exit("Aucune question récupérée : data.json n'est pas modifié.")

    present = [q["etape"] for q in questions]
    steps = [s for s in STEP_ORDER if s in present]
    steps += [s for s in dict.fromkeys(present) if s not in steps]

    data = {
        "mis_a_jour": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "etapes": steps,
        "questions": questions,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    print(f"{len(questions)} questions écrites dans {OUT}")


if __name__ == "__main__":
    main()
