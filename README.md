# Zeiterfassung

A self-hosted time tracking web app for Austrian employees. Built with Flask, SQLite, and Tailwind CSS — packaged as a single Docker container with no external dependencies.

---

## Features

### Monthly time tracking
- Enter arrival and departure times (two blocks per day, e.g. before/after lunch)
- Automatic IST calculation: `(Geht1 − Kommt1) + (Geht2 − Kommt2) − Pause`
- Running daily balance (IST − SOLL) shown inline
- Break warning (`⚠ zu kurz`) when the recorded break is shorter than the configured break rules require
- Times are entered as `HH:MM` (colon is inserted automatically); `00:00` is a valid time and shifts crossing midnight are handled
- Days without any input are not stored; clearing a day completely removes its entry
- Weekends visually darker; public holidays highlighted inline on the date cell

### SOLL calculation
- Per-weekday target minutes configured via work schedules (multiple schedules, each with a `valid_from` date)
- Day codes multiply the SOLL: `Urlaub` and `Krank` → 0× (no obligation), `Gleittag` and `Home Office` → 1× (full obligation)
- Public holiday factor applied on top (0 = free day, 0.5 = half day, 1 = full day)
- Special codes `NONE` / `XTRA` set SOLL = IST (neutral, no balance impact)

### Day codes
Built-in, non-deletable defaults:

| Code | Name | Faktor | Row colour |
|------|------|--------|------------|
| `K` | Krank | 0 | — |
| `U` | Urlaub | 0 | violet |
| `G` | Gleittag | 1 | teal |
| `HO` | Home Office | 1 | — |

Custom codes can be added and deleted freely; `U`, `G`, `K`, `HO` are protected.

### Balance / Saldo
- Resets to **zero on 1 January** each year — unused overtime does not carry forward
- Running balance is recalculated in the browser on every input change (no round-trip needed)
- The carryover into a month is calculated from all previous months of the same year

### Break rules (Pausenregeln)
- Managed under *Einstellungen → Pausenregeln*
- Each rule: "if the working time exceeds `HH:MM`, at least `N` minutes of break are required" (e.g. over 6:00 → 30 min)
- With multiple rules, the strictest applicable one wins; without any rule no break warning is shown

### Vacation tracking
- Vacation year starts on a configurable month (default: **March 1**), so January and February count against the *previous* year's allowance
- Per-year entitlement and carryover days configurable under *Einstellungen → Jahreseinstellungen*
- Half-day vacation code `UH` counts as 0.5 days
- Only weekdays are counted against the entitlement

### Public holidays
- Austrian public holidays are **automatically calculated** for any year using the Anonymous Gregorian Easter algorithm
- Navigating to a new year triggers holiday generation for that year
- Included: all 13 Austrian national holidays + Heiligabend (24.12, factor 0.5) + Silvester (31.12, factor 0.5)
- Holidays can be viewed, added, and deleted per year at *Feiertage*

### Annual overview (`/annual/<year>`)
- Bar chart of IST vs SOLL per month
- Month-by-month breakdown table with balance
- Vacation stats for the vacation year starting in the viewed calendar year

### Summary overview (`/summary`)
- All tracked calendar years in one table: IST, SOLL, balance, attendance days
- All vacation years: entitlement, carryover, days taken, days remaining
- Old years with zero activity are hidden automatically

### Backup — export & import
- *Einstellungen → Sicherung*: **Export herunterladen** downloads all data as a single JSON file (`zeiterfassung-backup-YYYY-MM-DD.json`) — time entries, settings, work schedules, break rules, day codes, holidays and per-year vacation settings
- **Importieren** restores such a file and **replaces** the existing data of every table contained in the backup
- The import runs in a single transaction: an invalid or corrupt file leaves the database unchanged
- Built-in day codes (`U`, `G`, `K`, `HO`) are recreated after an import if the backup didn't contain them
- Alternatively, stop the container and copy `./data/timetracking.db` for a raw file backup

---

## Tech stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12 · Flask 3.x |
| Database | SQLite (raw `sqlite3`, no ORM) |
| Frontend | Tailwind CSS (CDN play mode) · Vanilla JS |
| Server | Gunicorn (4 workers) |
| Container | Docker · docker-compose |

---

## Running with Docker

### Prebuilt image (GitHub Container Registry)

Every push to `main` builds a multi-arch image (`linux/amd64`, `linux/arm64`) via GitHub Actions and publishes it to:

```
ghcr.io/n0ne117/timetracking:latest
```

Tags: `latest` (current `main`), `sha-<commit>` for every build, and `X.Y.Z` / `X.Y` when a `vX.Y.Z` git tag is pushed.

The package is private (like the repository), so log in once with a GitHub personal access token that has the `read:packages` scope:

```bash
echo <TOKEN> | docker login ghcr.io -u n0ne117 --password-stdin
```

Then run it without the source code, e.g. with this `docker-compose.yml`:

```yaml
services:
  timetracking:
    image: ghcr.io/n0ne117/timetracking:latest
    ports:
      - "5050:5000"
    volumes:
      - ./data:/data
    environment:
      - SECRET_KEY=change-me-in-production
    restart: always
```

```bash
docker compose pull && docker compose up -d   # also the update command
```

### Building locally

```bash
# Clone / copy the project
git clone <repo-url>
cd TimeTracking

# Start
docker compose up -d

# The app is available at http://localhost:5050
```

The SQLite database is stored in `./data/timetracking.db` (bind-mounted from the host). The container restarts automatically.

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_PATH` | `/data/timetracking.db` | Path to the SQLite database inside the container |
| `SECRET_KEY` | `change-me-in-production` | Flask session secret — **change this** |

Set `SECRET_KEY` in `docker-compose.yml` before deploying:

```yaml
environment:
  - SECRET_KEY=your-random-secret-here
```

---

## First run

1. Open `http://localhost:5050` — you land on the current month.
2. Go to **Einstellungen** and configure:
   - *SOLL-Arbeitszeiten* — your weekly schedule (hours and minutes per weekday; leave days empty for 0)
   - *Pausenregeln* — break thresholds (e.g. 30 min required after 6 h work)
   - *Urlaubsanspruch Standard* — default vacation days per year
   - *Urlaubsjahr beginnt im Monat* — month when your vacation allocation refreshes
3. Go to **Feiertage** — Austrian holidays for the current year are pre-populated. Navigate to other years with the arrows; holidays are generated automatically.
4. Start entering times.

---

## Data model

```
settings          key/value store for global configuration
work_schedules    per-weekday target minutes, effective from a given date
break_rules       minimum break required after N minutes of work
day_codes         code → name + SOLL factor
holidays          date → name + SOLL factor (per calendar year)
time_entries      one row per calendar day (arrived1/left1/arrived2/left2/break/code/notes)
year_settings     per-year vacation entitlement and carryover
```

---

## Project structure

```
TimeTracking/
├── app.py              # Flask routes
├── models.py           # DB layer + all calculation logic
├── templates/
│   ├── base.html       # Navigation shell
│   ├── month.html      # Monthly time entry view
│   ├── annual.html     # Annual overview + chart
│   ├── summary.html    # Multi-year summary
│   ├── settings.html   # All configuration
│   ├── holidays.html   # Holiday management
├── static/
│   └── favicon.svg
├── requirements.txt
├── Dockerfile
└── docker-compose.yml
```

---

## License

MIT
