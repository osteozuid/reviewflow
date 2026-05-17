import csv
import io
import re
from pathlib import Path
from datetime import datetime

ENCODINGS_TO_TRY = ['utf-8-sig', 'utf-8', 'cp1252', 'latin-1']
REQUIRED_COLUMNS = {'agenda', 'datum', 'naam', 'e-mail', 'afspraak type'}
EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')


def try_parse_date(date_str):
    if not date_str:
        return None
    date_str = str(date_str).strip()
    for fmt in ('%d.%m.%Y', '%Y-%m-%d', '%d/%m/%Y'):
        try:
            return datetime.strptime(date_str, fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return None


def normalize_email(email):
    return str(email).strip().lower() if email else ''


def extract_voornaam(naam):
    if not naam:
        return ''
    parts = naam.strip().split()
    return parts[-1] if parts else ''


def extract_achternaam(naam):
    if not naam:
        return ''
    parts = naam.strip().split()
    return ' '.join(parts[:-1]) if len(parts) > 1 else naam.strip()


def is_valid_email(email):
    return bool(EMAIL_REGEX.match(email))


def _read_file(path):
    for encoding in ENCODINGS_TO_TRY:
        try:
            with open(path, encoding=encoding, newline='') as f:
                content = f.read()
            return content, encoding
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise ValueError(f"Kan bestand niet decoderen: {path}")


def _xlsx_to_csv_string(filepath):
    import openpyxl
    wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
    ws = wb.active
    lines = []
    for row in ws.iter_rows(values_only=True):
        lines.append(';'.join(
            str(cell).strip() if cell is not None else ''
            for cell in row
        ))
    wb.close()
    return '\n'.join(lines), 'xlsx'


def _process_csv_content(content, filename):
    reader = csv.DictReader(io.StringIO(content), delimiter=';')

    if not reader.fieldnames:
        raise ValueError(f"Geen kolomnamen gevonden in {filename}")

    fieldnames_normalized = {f.strip().lower(): f for f in reader.fieldnames if f}
    missing = REQUIRED_COLUMNS - set(fieldnames_normalized.keys())
    if missing:
        raise ValueError(f"Ontbrekende kolommen in {filename}: {missing}")

    stats = {
        'bestand': filename,
        'encoding': 'csv',
        'rijen_gelezen': 0, 'rijen_leeg': 0,
        'rijen_geen_naam': 0, 'rijen_geen_email': 0, 'rijen_ongeldig_email': 0,
        'rijen_ok': 0, 'overgeslagen': [],
    }

    rows = []
    for raw_row in reader:
        stats['rijen_gelezen'] += 1
        row = {k.strip().lower(): (v.strip() if v else '') for k, v in raw_row.items() if k}

        naam = row.get('naam', '').strip()
        email = normalize_email(row.get('e-mail', ''))
        afspraak_type_raw = row.get('afspraak type', '').strip()
        afspraak_type = afspraak_type_raw.lower()
        datum = row.get('datum', '').strip()

        if not naam and not email and not afspraak_type:
            stats['rijen_leeg'] += 1
            continue

        if not naam:
            stats['rijen_geen_naam'] += 1
            stats['overgeslagen'].append({'naam': '(leeg)', 'email': email or '(leeg)', 'reden': 'Geen naam'})
            continue

        if not email:
            stats['rijen_geen_email'] += 1
            stats['overgeslagen'].append({'naam': naam, 'email': '(leeg)', 'reden': 'Geen e-mail'})
            continue

        if not is_valid_email(email):
            stats['rijen_ongeldig_email'] += 1
            stats['overgeslagen'].append({'naam': naam, 'email': email, 'reden': 'Ongeldig e-mail'})
            continue

        stats['rijen_ok'] += 1
        rows.append({
            'naam': naam,
            'voornaam': extract_voornaam(naam),
            'achternaam': extract_achternaam(naam),
            'email': email,
            'geboortedatum': try_parse_date(row.get('geboortedatum', '')),
            'datum_consult': try_parse_date(datum),
            'telefoon': row.get('telefoon', '').strip(),
            'gsm': row.get('gsm nummer', '').strip(),
            'agenda': row.get('agenda', '').strip(),
            'afspraak_type': afspraak_type_raw,
            'bestand': filename,
        })

    return rows, stats


def load_csv_file(filepath):
    path = Path(filepath)
    if path.suffix.lower() in ('.xlsx', '.xls'):
        content, _ = _xlsx_to_csv_string(path)
        rows, stats = _process_csv_content(content, path.name)
        stats['encoding'] = 'xlsx'
    else:
        content, encoding = _read_file(path)
        rows, stats = _process_csv_content(content, path.name)
        stats['encoding'] = encoding
    return rows, stats


def load_all_csv(input_dir):
    input_dir = Path(input_dir)
    files = sorted([
        f for f in input_dir.iterdir()
        if f.suffix.lower() in ('.csv', '.xlsx', '.xls')
    ])

    if not files:
        raise FileNotFoundError(
            f"Geen bestanden gevonden in '{input_dir.resolve()}'.\n"
            "Upload eerst een CSV of Excel export."
        )

    all_rows, all_stats = [], []
    for f in files:
        rows, stats = load_csv_file(f)
        all_rows.extend(rows)
        all_stats.append(stats)

    return all_rows, all_stats
