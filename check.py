"""Starttijd-checker voor Golfbaan Delfland.

Leest config.json, kijkt in het openbare starttijdenoverzicht van Delfland
(IntoGolf) of er een vrije tijd is die past, en stuurt dan een pushmelding
via ntfy. Elke vrije tijd wordt maar één keer gemeld (zie gezien.json).
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import date

API = "https://delfland.baan.intogolf.nl/api/"
BOEKEN = "https://www.delflandgolf.nl/reserveer-starttijd/reserveren-starttijd/"
HIER = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HIER, "config.json")
GEZIEN = os.path.join(HIER, "gezien.json")


def get_json(path, params=None):
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "starttijd-checker"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def minuten(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def tijd(mins):
    return f"{mins // 60:02d}:{mins % 60:02d}"


def vrije_tijden(zoek, banen):
    """Geef [(lus, tijd, vrije plekken)] die passen bij een zoekopdracht."""
    van, tot = minuten(zoek["van"]), minuten(zoek["tot"])
    lussen = {l.lower() for l in zoek.get("lussen", [])}
    nodig = int(zoek.get("spelers", 1))
    holes = int(zoek.get("holes", 9))
    gevonden = []
    for baan in banen:
        naam = (baan.get("crlNameInternet") or baan.get("crlName") or "").lower()
        if lussen and naam not in lussen:
            continue
        times = baan.get("times") or []
        if isinstance(times, dict):
            times = list(times.values())
        for t in times:
            start = t.get("sttTimeFrom")
            if start is None or not (van <= start <= tot):
                continue
            if holes == 18 and not (t.get("sttCrlNrNext") or 0) > 0:
                continue
            vrij = (t.get("sttMaxPlayers") or 0) - (t.get("playerCount") or 0)
            if vrij >= nodig:
                gevonden.append((naam, tijd(start), vrij))
    return sorted(gevonden, key=lambda x: (x[1], x[0]))


def meld(onderwerp, tekst):
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        print("Geen NTFY_TOPIC ingesteld; melding alleen in de log:\n", onderwerp, "\n", tekst)
        return
    req = urllib.request.Request(
        "https://ntfy.sh/" + urllib.parse.quote(topic),
        data=tekst.encode("utf-8"),
        headers={"Title": onderwerp.encode("ascii", "ignore").decode(), "Click": BOEKEN,
                 "Tags": "golf", "Priority": "high"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=30).read()


def main():
    with open(CONFIG, encoding="utf-8") as f:
        zoekopdrachten = json.load(f).get("zoekopdrachten", [])
    gezien = {}
    if os.path.exists(GEZIEN):
        with open(GEZIEN, encoding="utf-8") as f:
            gezien = json.load(f)

    vandaag = date.today().isoformat()
    actief = [z for z in zoekopdrachten if z.get("datum", "") >= vandaag]
    if not actief:
        print("Geen zoekopdrachten voor vandaag of later.")

    locaties = get_json("igg/locations") if actief else []
    loc_nr = next((l["locNr"] for l in locaties if l.get("locName", "").lower() == "delfland"),
                  locaties[0]["locNr"] if locaties else None)

    nieuw_gezien = {}
    cache = {}
    for zoek in actief:
        d = zoek["datum"]
        if d not in cache:
            data = get_json("igg", {"date": d, "locNr": loc_nr})
            cache[d] = data.get("payload", data) if isinstance(data, dict) else data
        treffers = vrije_tijden(zoek, cache[d])
        sleutels = [f"{d}|{lus}|{t}" for lus, t, _ in treffers]
        oud = set(gezien.get(zoek["naam"], []))
        nieuw = [tr for tr, k in zip(treffers, sleutels) if k not in oud]
        nieuw_gezien[zoek["naam"]] = sleutels  # alleen wat nu vrij is; komt iets opnieuw vrij, dan weer melden
        print(f"{zoek['naam']}: {len(treffers)} passend, {len(nieuw)} nieuw")
        if nieuw:
            regels = "\n".join(f"{t}  {lus}  ({vrij} plek{'ken' if vrij != 1 else ''} vrij)" for lus, t, vrij in nieuw)
            meld(f"Starttijd vrij: {zoek['naam']}",
                 f"{d}, {zoek.get('holes', 9)} holes\n{regels}\nTik om te boeken (of open de Delfland-app).")

    if nieuw_gezien != gezien:
        with open(GEZIEN, "w", encoding="utf-8") as f:
            json.dump(nieuw_gezien, f, indent=1, ensure_ascii=False)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # zichtbaar in GitHub, maar de taak niet laten crashen
        print("Fout:", e, file=sys.stderr)
        sys.exit(1)
