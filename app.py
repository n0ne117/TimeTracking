import calendar
import json
import os
from datetime import date, datetime
from flask import Flask, Response, render_template, redirect, url_for, request, flash, jsonify
import models

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'timetracking-secret-key-2026')
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024  # backup uploads

# Initialise DB on startup (safe to call multiple times)
models.init_db()
models.ensure_austrian_holidays(date.today().year)

MONTH_NAMES = ['', 'Januar', 'Februar', 'März', 'April', 'Mai', 'Juni',
               'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember']
WEEKDAY_NAMES_SHORT = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']
WEEKDAY_NAMES_FULL = ['Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag']


@app.template_filter('fmt_minutes')
def fmt_minutes_filter(mins, show_sign=False):
    return models.format_minutes(mins, show_sign)


@app.context_processor
def inject_nav_year():
    """Make nav_year available in all templates for the Jahresübersicht nav link."""
    year = None
    if request.view_args:
        year = request.view_args.get('year')
    if year is None:
        year = date.today().year
    return {'nav_year': year}


@app.route('/')
def index():
    today = date.today()
    return redirect(url_for('month_view', year=today.year, month=today.month))


@app.route('/<int:year>/<int:month>', methods=['GET', 'POST'])
def month_view(year, month):
    if month < 1 or month > 12:
        return redirect(url_for('index'))

    if request.method == 'POST':
        days_in_month = calendar.monthrange(year, month)[1]
        for day in range(1, days_in_month + 1):
            entry = {
                'arrived1': request.form.get(f'arrived1_{day}') or None,
                'left1': request.form.get(f'left1_{day}') or None,
                'arrived2': request.form.get(f'arrived2_{day}') or None,
                'left2': request.form.get(f'left2_{day}') or None,
                'break_minutes': int(request.form.get(f'break_{day}') or 0),
                'code': request.form.get(f'code_{day}') or None,
                'notes': request.form.get(f'notes_{day}') or None,
            }
            models.upsert_time_entry(f"{year}-{month:02d}-{day:02d}", entry)
        flash('Erfolgreich gespeichert!', 'success')
        return redirect(url_for('month_view', year=year, month=month))

    models.ensure_austrian_holidays(year)

    settings = models.get_settings()
    work_schedules = models.get_work_schedules()
    holidays_map = models.get_holidays_map()
    day_codes = models.get_day_codes()
    break_rules = models.get_break_rules()

    # Carryover from previous month — resets to 0 on 1 January each year
    if month > 1:
        carryover = models.get_carryover(year, month - 1, work_schedules, holidays_map, day_codes, settings)
    else:
        carryover = 0

    days_in_month = calendar.monthrange(year, month)[1]
    entries = models.get_month_entries(year, month)
    today = date.today()

    days = []
    running_balance = carryover
    month_ist = 0
    month_soll = 0

    for d in range(1, days_in_month + 1):
        date_obj = date(year, month, d)
        date_str = date_obj.isoformat()
        weekday = date_obj.weekday()
        entry = entries.get(date_str) or {}
        holiday = holidays_map.get(date_str)
        calc = models.calculate_entry(entry, date_obj, work_schedules, holidays_map, day_codes, break_rules)
        running_balance += calc['diff_minutes']
        month_ist += calc['ist_minutes']
        month_soll += calc['soll_minutes']

        days.append({
            'day': d,
            'date': date_obj,
            'date_str': date_str,
            'weekday_short': WEEKDAY_NAMES_SHORT[weekday],
            'weekday_idx': weekday,
            'is_weekend': weekday >= 5,
            'holiday': holiday,
            'entry': entry,
            'calc': calc,
            'running_balance': running_balance,
            'is_today': date_obj == today,
        })

    vacation_stats = models.get_vacation_stats(year, month, settings)

    # Prev/next month navigation
    if month > 1:
        prev_nav = (year, month - 1)
    else:
        prev_nav = (year - 1, 12)
    if month < 12:
        next_nav = (year, month + 1)
    else:
        next_nav = (year + 1, 1)

    # Build code factors JSON for client-side calculations
    code_factors_json = json.dumps({c: info['factor'] for c, info in day_codes.items()})

    # Build holiday factors JSON
    holidays_json = json.dumps({k: v['factor'] for k, v in holidays_map.items()})

    # Build base minutes per date JSON for client-side use
    base_mins = {}
    for d_info in days:
        base_mins[d_info['date_str']] = d_info['calc']['base_minutes']
    base_mins_json = json.dumps(base_mins)

    return render_template('month.html',
        year=year, month=month,
        month_name=MONTH_NAMES[month],
        month_names=MONTH_NAMES,
        days=days,
        settings=settings,
        day_codes=day_codes,
        carryover=carryover,
        month_ist=month_ist,
        month_soll=month_soll,
        month_diff=month_ist - month_soll,
        final_balance=running_balance,
        vacation_stats=vacation_stats,
        prev_nav=prev_nav,
        next_nav=next_nav,
        today=today,
        code_factors_json=code_factors_json,
        holidays_json=holidays_json,
        base_mins_json=base_mins_json,
    )


@app.route('/settings', methods=['GET', 'POST'])
def settings_view():
    if request.method == 'POST':
        action = request.form.get('action', 'save_settings')

        if action == 'save_settings':
            models.save_settings({
                'vacation_entitlement': int(request.form.get('vacation_entitlement') or 25),
                'vacation_start_month': int(request.form.get('vacation_start_month') or 3),
            })
            flash('Einstellungen gespeichert!', 'success')

        elif action == 'save_schedule':
            sid = request.form.get('schedule_id') or None
            models.upsert_work_schedule(
                sid,
                request.form.get('valid_from'),
                models.parse_hhmm(request.form.get('mon')),
                models.parse_hhmm(request.form.get('tue')),
                models.parse_hhmm(request.form.get('wed')),
                models.parse_hhmm(request.form.get('thu')),
                models.parse_hhmm(request.form.get('fri')),
                models.parse_hhmm(request.form.get('sat')),
                models.parse_hhmm(request.form.get('sun')),
            )
            flash('Arbeitszeit gespeichert!', 'success')

        elif action == 'delete_schedule':
            models.delete_work_schedule(request.form.get('schedule_id'))
            flash('Arbeitszeit gelöscht!', 'success')

        elif action == 'save_code':
            models.upsert_day_code(
                request.form.get('code', ''),
                request.form.get('code_name', ''),
                request.form.get('factor', '1'),
            )
            flash('Code gespeichert!', 'success')

        elif action == 'delete_code':
            code = request.form.get('code', '').upper()
            if models.delete_day_code(code):
                flash('Code gelöscht!', 'success')
            else:
                flash(f'Code {code} kann nicht gelöscht werden.', 'error')

        elif action == 'save_break_rule':
            min_work = models.parse_hhmm(request.form.get('min_work'))
            req_break = request.form.get('required_break', '').strip()
            if min_work is None or not req_break.isdigit():
                flash('Bitte Arbeitszeit (HH:MM) und Pause (Minuten) angeben.', 'error')
            else:
                models.add_break_rule(min_work, int(req_break))
                flash('Pausenregel gespeichert!', 'success')

        elif action == 'delete_break_rule':
            models.delete_break_rule(request.form.get('rule_id'))
            flash('Pausenregel gelöscht!', 'success')

        elif action == 'save_year_setting':
            ys_year_raw = request.form.get('ys_year', '').strip()
            if not ys_year_raw.isdigit():
                flash('Bitte ein gültiges Jahr angeben.', 'error')
            else:
                models.upsert_year_setting(
                    int(ys_year_raw),
                    request.form.get('ys_entitlement') or None,
                    request.form.get('ys_carryover') or 0,
                )
                flash('Jahreseinstellung gespeichert!', 'success')

        elif action == 'delete_year_setting':
            models.delete_year_setting(request.form.get('ys_year'))
            flash('Jahreseinstellung gelöscht!', 'success')

        return redirect(url_for('settings_view'))

    settings = models.get_settings()
    work_schedules = models.get_work_schedules()
    day_codes = models.get_day_codes()
    break_rules = models.get_break_rules()
    year_settings_list = models.get_all_year_settings()
    return render_template('settings.html',
        settings=settings,
        work_schedules=work_schedules,
        day_codes=day_codes,
        break_rules=break_rules,
        protected_codes=models.PROTECTED_CODES,
        weekday_names=WEEKDAY_NAMES_FULL,
        current_year=date.today().year,
        year_settings_list=year_settings_list,
    )


@app.route('/export')
def export_view():
    data = models.export_data()
    filename = f"zeiterfassung-backup-{date.today().isoformat()}.json"
    return Response(
        json.dumps(data, ensure_ascii=False, indent=2),
        mimetype='application/json',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


@app.route('/import', methods=['POST'])
def import_view():
    upload = request.files.get('backup_file')
    if not upload or not upload.filename:
        flash('Bitte eine Sicherungsdatei auswählen.', 'error')
        return redirect(url_for('settings_view'))
    try:
        payload = json.load(upload.stream)
        counts = models.import_data(payload)
    except (ValueError, UnicodeDecodeError) as e:
        # json.JSONDecodeError is a ValueError subclass
        msg = str(e) if not isinstance(e, json.JSONDecodeError) else 'Datei ist kein gültiges JSON.'
        flash(f'Import fehlgeschlagen: {msg}', 'error')
        return redirect(url_for('settings_view'))
    except Exception as e:
        flash(f'Import fehlgeschlagen: {e}', 'error')
        return redirect(url_for('settings_view'))
    flash(f"Import erfolgreich: {counts.get('time_entries', 0)} Zeiteinträge, "
          f"{sum(counts.values())} Datensätze insgesamt.", 'success')
    return redirect(url_for('settings_view'))


@app.route('/holidays', methods=['GET', 'POST'])
def holidays_view():
    if request.method == 'POST':
        year = int(request.form.get('year') or date.today().year)
        action = request.form.get('action', 'add')
        if action == 'add':
            date_str = request.form.get('date', '')
            name = request.form.get('name', '')
            factor = float(request.form.get('factor', 0))
            if date_str and name:
                models.upsert_holiday(date_str, name, factor)
                flash('Feiertag gespeichert!', 'success')
            else:
                flash('Datum und Name sind Pflichtfelder.', 'error')
        elif action == 'delete':
            models.delete_holiday(request.form.get('date', ''))
            flash('Feiertag gelöscht!', 'success')
        return redirect(url_for('holidays_view', year=year))

    year = request.args.get('year', type=int) or date.today().year
    models.ensure_austrian_holidays(year)
    settings = models.get_settings()
    holidays = models.get_holidays_for_year(year)
    return render_template('holidays.html',
        holidays=holidays,
        settings=settings,
        year=year,
    )


@app.route('/annual/<int:year>')
def annual_view(year):
    models.ensure_austrian_holidays(year)

    settings = models.get_settings()
    work_schedules = models.get_work_schedules()
    holidays_map = models.get_holidays_map()
    day_codes = models.get_day_codes()
    break_rules = models.get_break_rules()
    # Annual view always shows the vacation year that starts in this calendar year
    start_m = int(settings.get('vacation_start_month', 3))
    vacation_stats = models.get_vacation_stats(year, start_m, settings)

    months_data = []
    carryover = 0  # Overtime resets to zero each year
    year_ist = 0
    year_soll = 0

    for m in range(1, 13):
        days_in_month = calendar.monthrange(year, m)[1]
        entries = models.get_month_entries(year, m)
        month_ist = 0
        month_soll = 0
        attendance_days = 0

        for d in range(1, days_in_month + 1):
            date_obj = date(year, m, d)
            entry = entries.get(date_obj.isoformat()) or {}
            calc = models.calculate_entry(entry, date_obj, work_schedules, holidays_map, day_codes, break_rules)
            month_ist += calc['ist_minutes']
            month_soll += calc['soll_minutes']
            if calc['ist_minutes'] > 0:
                attendance_days += 1

        month_diff = month_ist - month_soll
        end_balance = carryover + month_diff
        months_data.append({
            'month': m,
            'name': MONTH_NAMES[m],
            'ist': month_ist,
            'soll': month_soll,
            'diff': month_diff,
            'start_balance': carryover,
            'end_balance': end_balance,
            'attendance_days': attendance_days,
        })
        year_ist += month_ist
        year_soll += month_soll
        carryover = end_balance

    return render_template('annual.html',
        year=year,
        months_data=months_data,
        year_ist=year_ist,
        year_soll=year_soll,
        year_diff=year_ist - year_soll,
        final_balance=carryover,
        vacation_stats=vacation_stats,
        settings=settings,
        month_names=MONTH_NAMES,
    )


@app.route('/summary')
def summary_view():
    settings = models.get_settings()
    work_schedules = models.get_work_schedules()
    holidays_map = models.get_holidays_map()
    day_codes = models.get_day_codes()
    break_rules = models.get_break_rules()
    start_m = int(settings.get('vacation_start_month', 3))

    years = models.get_all_tracked_years()

    current_year = date.today().year
    first_year = min(years) if years else current_year

    years_data = []
    vac_years_seen = set()
    vac_years_data = []

    for year in years:
        models.ensure_austrian_holidays(year)
        year_ist = 0
        year_soll = 0
        attendance_days = 0
        for m in range(1, 13):
            days_in_month = calendar.monthrange(year, m)[1]
            entries = models.get_month_entries(year, m)
            for d in range(1, days_in_month + 1):
                date_obj = date(year, m, d)
                entry = entries.get(date_obj.isoformat()) or {}
                calc = models.calculate_entry(entry, date_obj, work_schedules, holidays_map, day_codes, break_rules)
                year_ist += calc['ist_minutes']
                year_soll += calc['soll_minutes']
                if calc['ist_minutes'] > 0:
                    attendance_days += 1

        # Hide stale years that have zero activity and aren't the current/future year
        if year_ist == 0 and year_soll == 0 and year < current_year:
            continue

        years_data.append({
            'year': year,
            'ist': year_ist,
            'soll': year_soll,
            'diff': year_ist - year_soll,
            'attendance_days': attendance_days,
        })

        # Vacation year starting in this calendar year
        vs = models.get_vacation_stats(year, start_m, settings)
        vy = vs['vac_year']
        if vy not in vac_years_seen:
            vac_years_seen.add(vy)
            vac_years_data.append(vs)

        # Pre-start-month vacation year — only relevant for the first tracked year
        # (for all later years, the previous year's main vac_year already covers this)
        if year == first_year and start_m > 1:
            vs_pre = models.get_vacation_stats(year, 1, settings)
            vy_pre = vs_pre['vac_year']
            if vy_pre not in vac_years_seen:
                vac_years_seen.add(vy_pre)
                vac_years_data.append(vs_pre)

    # Hide old vacation years with no activity
    vac_years_data = [vs for vs in vac_years_data
                      if vs['used'] > 0 or vs['vac_year'] >= current_year - 1]
    vac_years_data.sort(key=lambda v: v['vac_year'])

    return render_template('summary.html',
        years_data=years_data,
        vac_years_data=vac_years_data,
        settings=settings,
    )


if __name__ == '__main__':
    import os
    models.init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)
