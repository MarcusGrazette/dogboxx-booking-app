"""
Run a read-only SQL file against the database configured by DATABASE_URL,
printing results as an aligned table.  Loads .env so it works the same way
the app does.

Usage:
    venv/bin/python scripts/run_sql.py scripts/find_orphaned_bookings.sql

Optional --db override (handy for prod):
    venv/bin/python scripts/run_sql.py scripts/find_orphaned_bookings.sql \
        --db "postgresql://..."

Refuses to run files containing INSERT/UPDATE/DELETE/DROP/TRUNCATE/ALTER —
this is intentionally for diagnostics, not mutations.
"""
import argparse
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
import psycopg2


DESTRUCTIVE = re.compile(
    r'\b(insert|update|delete|drop|truncate|alter|grant|revoke)\b',
    re.IGNORECASE,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sql_file', type=Path)
    parser.add_argument('--db', default=None,
                        help='Override DATABASE_URL (e.g. for prod).')
    args = parser.parse_args()

    load_dotenv()
    db_url = args.db or os.environ.get('DATABASE_URL')
    if not db_url:
        sys.exit('DATABASE_URL not set (check .env or pass --db).')

    sql = args.sql_file.read_text()
    # Strip comments before scanning for destructive verbs so that comment
    # text doesn't trigger a false positive.
    stripped = re.sub(r'--[^\n]*', '', sql)
    if DESTRUCTIVE.search(stripped):
        sys.exit('Refusing to run: file contains a write/DDL statement. '
                 'This runner is read-only by design.')

    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            if cur.description is None:
                print('(no rows returned)')
                return
            cols = [c.name for c in cur.description]
            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        print(f'(0 rows — {", ".join(cols)})')
        return

    # Pretty-print: pad each column to the max width in that column.
    str_rows = [[('' if v is None else str(v)) for v in row] for row in rows]
    widths = [max(len(cols[i]), *(len(r[i]) for r in str_rows))
              for i in range(len(cols))]
    fmt = ' | '.join(f'{{:<{w}}}' for w in widths)
    print(fmt.format(*cols))
    print('-+-'.join('-' * w for w in widths))
    for row in str_rows:
        print(fmt.format(*row))
    print(f'\n({len(rows)} rows)')


if __name__ == '__main__':
    main()
