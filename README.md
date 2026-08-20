# Lager- & Umsatz-Tool (Calculator)

Kleine Webseite fuer die Admins: Kundenname + Produkt + Menge eintragen →
zieht automatisch vom Lager ab und zeigt **Umsatz heute** und **Umsatz gesamt**.
Alle mit Link + Passwort sehen dieselben Zahlen (server-basiert).

- Nur **Python 3** noetig, keine externen Pakete.
- Daten liegen in `admin.db` (per `DB_PATH` aenderbar).
- Produkte/Preise aus der Shop-config uebernommen.

## Auf Railway deployen

1. Railway → **New Project → Deploy from GitHub repo** → **`Specidjegst/Calculator`**.
   (Repo ist oeffentlich – falls es nicht auftaucht, bei „Configure GitHub App"
   Zugriff geben.)
2. Reiter **Variables** → **New Variable**:
   - `ADMIN_PW` = dein Passwort (zum Einloggen)
3. **Settings → Networking → Generate Domain** → oeffentliche URL
   (`…up.railway.app`). Fertig – Link in die Gruppe posten.

Root Directory muss NICHT gesetzt werden (Dateien liegen im Wurzelverzeichnis;
`Procfile` + `requirements.txt` sorgen fuer die Python-Erkennung + Start).

### Daten dauerhaft machen (empfohlen)

Ohne Volume wird `admin.db` bei jedem Redeploy zurueckgesetzt:

1. **Settings → Volumes → New Volume**, Mount-Pfad `/data`.
2. **Variables** → `DB_PATH` = `/data/admin.db`.

## Lokal testen

```bash
ADMIN_PW="test123" python admin.py
# dann http://localhost:8080 oeffnen
```
