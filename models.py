import sqlite3
import os
from datetime import date, datetime, timedelta
from contextlib import contextmanager

DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(__file__), 'data', 'timetracking.db'))


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_db() as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS work_schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                valid_from TEXT NOT NULL,
                mon_minutes INTEGER DEFAULT 0,
                tue_minutes INTEGER DEFAULT 0,
                wed_minutes INTEGER DEFAULT 0,
                thu_minutes INTEGER DEFAULT 0,
                fri_minutes INTEGER DEFAULT 0,
                sat_minutes INTEGER DEFAULT 0,
                sun_minutes INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS break_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                min_work_minutes INTEGER NOT NULL,
                required_break_minutes INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS day_codes (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                factor TEXT NOT NULL DEFAULT '1'
            );

            CREATE TABLE IF NOT EXISTS holidays (
                date TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                factor REAL NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS time_entries (
                date TEXT PRIMARY KEY,
                arrived1 TEXT,
                left1 TEXT,
                arrived2 TEXT,
                left2 TEXT,
                break_minutes INTEGER DEFAULT 0,
                code TEXT,
                notes TEXT
            );

            CREATE TABLE IF NOT EXISTS year_settings (
                year INTEGER PRIMARY KEY,
                vacation_entitlement INTEGER,
                vacation_carryover INTEGER NOT NULL DEFAULT 0
            );

            INSERT OR IGNORE INTO day_codes (code, name, factor) VALUES ('K',  'Krank',       '0');
            INSERT OR IGNORE INTO day_codes (code, name, factor) VALUES ('U',  'Urlaub',      '0');
            INSERT OR IGNORE INTO day_codes (code, name, factor) VALUES ('G',  'Gleittag',    '1');
            INSERT OR IGNORE INTO day_codes (code, name, factor) VALUES ('HO', 'Home Office', '1');
        ''')


# ─── Settings ────────────────────────────────────────────────────────────────

def get_settings():
    with get_db() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {r['key']: r['value'] for r in rows}


def set_setting(key, value):
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))


def save_settings(data: dict):
    with get_db() as conn:
        for key, value in data.items():
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))


# ─── Work Schedules ───────────────────────────────────────────────────────────

WEEKDAY_COLS = ['mon_minutes', 'tue_minutes', 'wed_minutes', 'thu_minutes',
                'fri_minutes', 'sat_minutes', 'sun_minutes']

def get_work_schedules():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM work_schedules ORDER BY valid_from").fetchall()
    schedules = [dict(r) for r in rows]
    # Older rows may hold NULL for days left empty — treat them as 0
    for sched in schedules:
        for col in WEEKDAY_COLS:
            sched[col] = sched[col] or 0
    return schedules


def upsert_work_schedule(schedule_id, valid_from, mon, tue, wed, thu, fri, sat, sun):
    mon, tue, wed, thu, fri, sat, sun = (m or 0 for m in (mon, tue, wed, thu, fri, sat, sun))
    with get_db() as conn:
        if schedule_id:
            conn.execute(
                "UPDATE work_schedules SET valid_from=?,mon_minutes=?,tue_minutes=?,wed_minutes=?,thu_minutes=?,fri_minutes=?,sat_minutes=?,sun_minutes=? WHERE id=?",
                (valid_from, mon, tue, wed, thu, fri, sat, sun, schedule_id)
            )
        else:
            conn.execute(
                "INSERT INTO work_schedules (valid_from,mon_minutes,tue_minutes,wed_minutes,thu_minutes,fri_minutes,sat_minutes,sun_minutes) VALUES (?,?,?,?,?,?,?,?)",
                (valid_from, mon, tue, wed, thu, fri, sat, sun)
            )


def delete_work_schedule(schedule_id):
    with get_db() as conn:
        conn.execute("DELETE FROM work_schedules WHERE id=?", (schedule_id,))


# ─── Break Rules ─────────────────────────────────────────────────────────────

def get_break_rules():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM break_rules ORDER BY min_work_minutes").fetchall()
    return [dict(r) for r in rows]


def add_break_rule(min_work_minutes, required_break_minutes):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO break_rules (min_work_minutes, required_break_minutes) VALUES (?, ?)",
            (int(min_work_minutes), int(required_break_minutes))
        )


def delete_break_rule(rule_id):
    with get_db() as conn:
        conn.execute("DELETE FROM break_rules WHERE id=?", (rule_id,))


# ─── Day Codes ────────────────────────────────────────────────────────────────

PROTECTED_CODES = ('U', 'G', 'K', 'HO')


def get_day_codes():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM day_codes ORDER BY code").fetchall()
    return {r['code']: dict(r) for r in rows}


def upsert_day_code(code, name, factor):
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO day_codes (code, name, factor) VALUES (?, ?, ?)",
            (code.upper(), name, factor)
        )


def delete_day_code(code):
    """Delete a custom day code. Returns False if the code is protected."""
    code = code.upper()
    if code in PROTECTED_CODES:
        return False
    with get_db() as conn:
        conn.execute("DELETE FROM day_codes WHERE code=?", (code,))
    return True


# ─── Holidays ─────────────────────────────────────────────────────────────────

def get_holidays_map():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM holidays ORDER BY date").fetchall()
    return {r['date']: dict(r) for r in rows}


def get_holidays_list():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM holidays ORDER BY date").fetchall()
    return [dict(r) for r in rows]


def get_all_tracked_years():
    """Return sorted list of calendar years that have any time_entries, plus the current year."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT CAST(substr(date, 1, 4) AS INTEGER) AS y "
            "FROM time_entries ORDER BY y"
        ).fetchall()
    years = [r['y'] for r in rows]
    current = date.today().year
    if current not in years:
        years.append(current)
        years.sort()
    return years


def get_holidays_for_year(year):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM holidays WHERE date LIKE ? ORDER BY date",
            (f"{year}-%",)
        ).fetchall()
    return [dict(r) for r in rows]


def upsert_holiday(date_str, name, factor):
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO holidays (date, name, factor) VALUES (?, ?, ?)",
            (date_str, name, float(factor))
        )


def delete_holiday(date_str):
    with get_db() as conn:
        conn.execute("DELETE FROM holidays WHERE date=?", (date_str,))


# ─── Time Entries ─────────────────────────────────────────────────────────────

def get_time_entry(date_str):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM time_entries WHERE date=?", (date_str,)).fetchone()
    return dict(row) if row else None


def get_month_entries(year, month):
    prefix = f"{year}-{month:02d}-"
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM time_entries WHERE date LIKE ? ORDER BY date",
            (prefix + '%',)
        ).fetchall()
    return {r['date']: dict(r) for r in rows}


def upsert_time_entry(date_str, data: dict):
    fields = ('arrived1', 'left1', 'arrived2', 'left2', 'break_minutes', 'code', 'notes')
    if not any(data.get(f) for f in fields):
        # Nothing entered for this day — don't keep an empty row around
        with get_db() as conn:
            conn.execute("DELETE FROM time_entries WHERE date=?", (date_str,))
        return
    with get_db() as conn:
        existing = conn.execute("SELECT 1 FROM time_entries WHERE date=?", (date_str,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE time_entries SET arrived1=?,left1=?,arrived2=?,left2=?,break_minutes=?,code=?,notes=? WHERE date=?",
                (
                    data.get('arrived1') or None,
                    data.get('left1') or None,
                    data.get('arrived2') or None,
                    data.get('left2') or None,
                    int(data.get('break_minutes') or 0),
                    data.get('code') or None,
                    data.get('notes') or None,
                    date_str
                )
            )
        else:
            conn.execute(
                "INSERT INTO time_entries (date,arrived1,left1,arrived2,left2,break_minutes,code,notes) VALUES (?,?,?,?,?,?,?,?)",
                (
                    date_str,
                    data.get('arrived1') or None,
                    data.get('left1') or None,
                    data.get('arrived2') or None,
                    data.get('left2') or None,
                    int(data.get('break_minutes') or 0),
                    data.get('code') or None,
                    data.get('notes') or None,
                )
            )


# ─── Year Settings ────────────────────────────────────────────────────────────

def get_year_setting(year):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM year_settings WHERE year=?", (int(year),)).fetchone()
    return dict(row) if row else None


def get_all_year_settings():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM year_settings ORDER BY year").fetchall()
    return [dict(r) for r in rows]


def upsert_year_setting(year, vacation_entitlement, vacation_carryover):
    ent = int(vacation_entitlement) if vacation_entitlement not in (None, '', 'None') else None
    vc = int(vacation_carryover) if vacation_carryover not in (None, '', 'None') else 0
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO year_settings (year, vacation_entitlement, vacation_carryover) VALUES (?, ?, ?)",
            (int(year), ent, vc)
        )


def delete_year_setting(year):
    with get_db() as conn:
        conn.execute("DELETE FROM year_settings WHERE year=?", (int(year),))


# ─── Calculations ─────────────────────────────────────────────────────────────

def parse_hhmm(s):
    """Return total minutes from 'HH:MM' string, or None if absent/invalid.

    Returns 0 (not None) for a genuine midnight value '00:00'.
    Callers must check `is not None` rather than truthiness.
    """
    if not s:
        return None
    try:
        h, m = s.split(':')
        return int(h) * 60 + int(m)
    except Exception:
        return None


def format_minutes(mins, show_sign=False):
    """Format minutes as H:MM string."""
    if mins is None:
        return '—'
    sign = ''
    if mins < 0:
        sign = '−'
    elif show_sign and mins > 0:
        sign = '+'
    abs_m = abs(int(mins))
    h = abs_m // 60
    m = abs_m % 60
    return f"{sign}{h}:{m:02d}"


def get_base_minutes_for_date(date_obj, work_schedules):
    """Get target work minutes for a given date based on the applicable work schedule."""
    weekday = date_obj.weekday()  # 0=Mon, 6=Sun
    applicable = None
    for sched in sorted(work_schedules, key=lambda s: s['valid_from']):
        if sched['valid_from'] <= date_obj.isoformat():
            applicable = sched
    if not applicable:
        return 0
    return applicable[WEEKDAY_COLS[weekday]] or 0


def calculate_entry(entry, date_obj, work_schedules, holidays_map, day_codes, break_rules=None):
    """
    Returns dict with:
      ist_minutes, soll_minutes, diff_minutes,
      break_warning (bool), base_minutes, holiday
    """
    entry = entry or {}
    base_minutes = get_base_minutes_for_date(date_obj, work_schedules)
    holiday = holidays_map.get(date_obj.isoformat())

    # IST: (left1-arrived1) + (left2-arrived2) - break
    raw_time = 0
    a1, l1 = parse_hhmm(entry.get('arrived1')), parse_hhmm(entry.get('left1'))
    a2, l2 = parse_hhmm(entry.get('arrived2')), parse_hhmm(entry.get('left2'))
    if a1 is not None and l1 is not None:
        diff1 = l1 - a1
        if diff1 < 0:
            diff1 += 24 * 60
        raw_time += diff1
    if a2 is not None and l2 is not None:
        diff2 = l2 - a2
        if diff2 < 0:
            diff2 += 24 * 60
        raw_time += diff2
    break_mins = int(entry.get('break_minutes') or 0)
    ist = max(0, raw_time - break_mins)

    # SOLL
    code = (entry.get('code') or '').upper()
    code_info = day_codes.get(code) if code else None
    factor_str = code_info['factor'] if code_info else None

    if holiday and not code:
        soll = round(holiday['factor'] * base_minutes)
    elif factor_str in ('NONE', 'XTRA'):
        soll = ist
    elif factor_str is not None:
        try:
            cf = float(factor_str)
            if holiday:
                soll = round(holiday['factor'] * cf * base_minutes)
            else:
                soll = round(cf * base_minutes)
        except ValueError:
            soll = base_minutes
    else:
        soll = base_minutes

    diff = ist - soll

    # Break warning: check if break is sufficient
    break_warning = False
    if raw_time > 0:
        if break_rules is None:
            break_rules = get_break_rules()
        required_break = 0
        for rule in break_rules:
            if raw_time > rule['min_work_minutes']:
                required_break = max(required_break, rule['required_break_minutes'])
        break_warning = (break_mins < required_break)

    return {
        'ist_minutes': ist,
        'soll_minutes': soll,
        'diff_minutes': diff,
        'base_minutes': base_minutes,
        'holiday': holiday,
        'break_warning': break_warning,
        'raw_time': raw_time,
    }


def get_carryover(year, month, work_schedules, holidays_map, day_codes, settings=None):
    """Compute the running balance at end of the given month within the same year.

    Overtime does not carry over between years — balance resets to 0 each January.
    """
    import calendar as cal_mod
    break_rules = get_break_rules()
    balance = 0
    for m in range(1, month + 1):
        days_in_month = cal_mod.monthrange(year, m)[1]
        entries = get_month_entries(year, m)
        for d in range(1, days_in_month + 1):
            date_obj = date(year, m, d)
            entry = entries.get(date_obj.isoformat())
            calc = calculate_entry(entry, date_obj, work_schedules, holidays_map, day_codes, break_rules)
            balance += calc['diff_minutes']
    return balance


def get_vacation_stats(year, month, settings):
    """Return vacation days used/remaining for the vacation year that covers year/month.

    The vacation year starts on vacation_start_month (default 3 = March) of year N
    and ends on the last day of (vacation_start_month - 1) of year N+1.

    For Jan/Feb the vacation year is year-1 (previous allocation still applies).
    For Mar-Dec the vacation year is year (fresh allocation just arrived).
    """
    import calendar as cal_mod

    start_m = int(settings.get('vacation_start_month', 3))

    # Which vacation year covers this month?
    vac_year = year if month >= start_m else year - 1

    # Vacation period: start_m/vac_year  →  (start_m-1)/vac_year+1  (or Dec if start_m==1)
    period_start = date(vac_year, start_m, 1)
    if start_m == 1:
        period_end = date(vac_year, 12, 31)
    else:
        end_m = start_m - 1
        end_y = vac_year + 1
        period_end = date(end_y, end_m, cal_mod.monthrange(end_y, end_m)[1])

    # Entitlement for this vacation year
    ys = get_year_setting(vac_year)
    global_entitlement = int(settings.get('vacation_entitlement', 25))
    entitlement = ys['vacation_entitlement'] if (ys and ys['vacation_entitlement'] is not None) else global_entitlement
    carryover_vacation = ys['vacation_carryover'] if ys else 0
    total = entitlement + carryover_vacation

    # Count U / UH days (weekdays only) across the entire vacation period
    used = 0.0
    cur = period_start
    while cur <= period_end:
        if cur.weekday() < 5:
            with get_db() as conn:
                row = conn.execute(
                    "SELECT code FROM time_entries WHERE date = ?",
                    (cur.isoformat(),)
                ).fetchone()
            if row:
                code = (row['code'] or '').upper()
                if code == 'U':
                    used += 1.0
                elif code == 'UH':
                    used += 0.5
        cur += timedelta(days=1)

    return {
        'vac_year': vac_year,
        'period_start': period_start,
        'period_end': period_end,
        'entitlement': entitlement,
        'carryover': carryover_vacation,
        'total': total,
        'used': used,
        'remaining': total - used,
    }


# ─── Backup / Restore ─────────────────────────────────────────────────────────

BACKUP_FORMAT = 'zeiterfassung-backup'
BACKUP_VERSION = 1
BACKUP_TABLES = ['settings', 'work_schedules', 'break_rules', 'day_codes', 'holidays',
                 'time_entries', 'year_settings']


def _table_columns(conn, table):
    return [r['name'] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def export_data():
    """Return all data as a JSON-serialisable dict."""
    tables = {}
    with get_db() as conn:
        for table in BACKUP_TABLES:
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            tables[table] = [dict(r) for r in rows]
    return {
        'format': BACKUP_FORMAT,
        'version': BACKUP_VERSION,
        'exported_at': datetime.now().isoformat(timespec='seconds'),
        'tables': tables,
    }


def import_data(payload):
    """Replace the contents of every table contained in the backup.

    Tables missing from the backup are left untouched. Unknown tables and
    columns are ignored. Runs in a single transaction — on any error nothing
    is changed. Returns {table: row_count} for the imported tables.
    """
    if not isinstance(payload, dict) or payload.get('format') != BACKUP_FORMAT:
        raise ValueError('Keine gültige Zeiterfassung-Sicherung.')
    if int(payload.get('version', 0)) > BACKUP_VERSION:
        raise ValueError('Sicherung stammt von einer neueren Version.')
    tables = payload.get('tables')
    if not isinstance(tables, dict):
        raise ValueError('Sicherung enthält keine Tabellen.')

    counts = {}
    with get_db() as conn:
        for table in BACKUP_TABLES:
            if table not in tables:
                continue
            rows = tables[table]
            if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
                raise ValueError(f'Tabelle {table} ist ungültig.')
            columns = _table_columns(conn, table)
            conn.execute(f"DELETE FROM {table}")
            for row in rows:
                cols = [c for c in columns if c in row]
                if not cols:
                    continue
                conn.execute(
                    f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                    [row[c] for c in cols]
                )
            counts[table] = len(rows)
    # Restore built-in day codes if the backup didn't contain them
    init_db()
    return counts


# ─── Austrian Public Holidays ─────────────────────────────────────────────────

def compute_easter(year):
    """Anonymous Gregorian algorithm — returns date of Easter Sunday."""
    a = year % 19
    b, c = year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def get_austrian_holidays(year):
    """Return list of (date_str, name, factor) for all Austrian public holidays."""
    easter = compute_easter(year)
    return [
        (date(year, 1, 1).isoformat(),              "Neujahr",               0.0),
        (date(year, 1, 6).isoformat(),              "Heilige Drei Könige",   0.0),
        ((easter + timedelta(days=1)).isoformat(),  "Ostermontag",           0.0),
        (date(year, 5, 1).isoformat(),              "Staatsfeiertag",        0.0),
        ((easter + timedelta(days=39)).isoformat(), "Christi Himmelfahrt",   0.0),
        ((easter + timedelta(days=50)).isoformat(), "Pfingstmontag",         0.0),
        ((easter + timedelta(days=60)).isoformat(), "Fronleichnam",          0.0),
        (date(year, 8, 15).isoformat(),             "Mariä Himmelfahrt",     0.0),
        (date(year, 10, 26).isoformat(),            "Nationalfeiertag",      0.0),
        (date(year, 11, 1).isoformat(),             "Allerheiligen",         0.0),
        (date(year, 12, 8).isoformat(),             "Maria Empfängnis",      0.0),
        (date(year, 12, 24).isoformat(),            "Weihnachten",           0.5),
        (date(year, 12, 25).isoformat(),            "Christtag",             0.0),
        (date(year, 12, 26).isoformat(),            "Stefanitag",            0.0),
        (date(year, 12, 31).isoformat(),            "Silvester",             0.5),
    ]


def ensure_austrian_holidays(year):
    """Auto-populate Austrian public holidays for the given year if none exist yet."""
    with get_db() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM holidays WHERE date LIKE ?",
            (f"{year}-%",)
        ).fetchone()[0]
    if count == 0:
        for date_str, name, factor in get_austrian_holidays(year):
            upsert_holiday(date_str, name, factor)
