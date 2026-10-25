#!/usr/bin/env python3
"""Copy Job Hunter SQLite rows into an already-provisioned PostgreSQL schema.

Safe by default: without --apply this script only reports what would be copied.
It never deletes target rows. Rows are inserted with ON CONFLICT DO NOTHING,
so reruns are non-destructive. Set SQLITE_PATH and DATABASE_URL in the shell;
do not put credentials in source control.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

TABLES = (
    "jobs",
    "hunt_runs",
    "settings",
    "candidate_profile",
    "application_drafts",
    "application_events",
    "gmail_tokens",
    "gmail_seen_messages",
)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", default=os.getenv("SQLITE_PATH", "job_hunter.db"),
                        help="Path to the old SQLite database (default: SQLITE_PATH or ./job_hunter.db)")
    parser.add_argument("--apply", action="store_true",
                        help="Actually copy rows. Without this flag, only prints a dry run.")
    parser.add_argument("--batch-size", type=int, default=250)
    return parser.parse_args()


def main():
    args = parse_args()
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        sys.exit("ERROR: DATABASE_URL is not set. Configure it in your shell/secure environment.")
    source_path = Path(args.sqlite)
    if not source_path.is_file():
        sys.exit(f"ERROR: SQLite file not found: {source_path.resolve()}")
    if args.batch_size < 1 or args.batch_size > 5000:
        sys.exit("ERROR: --batch-size must be between 1 and 5000")

    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row

    sqlite = sqlite3.connect(str(source_path))
    sqlite.row_factory = sqlite3.Row
    pg_url = database_url
    if pg_url.startswith("postgres://"):
        pg_url = "postgresql://" + pg_url[len("postgres://"):]
    with sqlite, psycopg.connect(pg_url, connect_timeout=20, sslmode="require", row_factory=dict_row) as pg:
        source_tables = {
            row["name"] for row in sqlite.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        present = [table for table in TABLES if table in source_tables]
        missing_source = [table for table in TABLES if table not in source_tables]
        print(f"SQLite source: {source_path.resolve()}")
        print(f"PostgreSQL target: {pg.info.host or 'configured host'} (credentials hidden)")
        if missing_source:
            print("Tables absent from SQLite source (skipped): " + ", ".join(missing_source))

        plans = []
        for table in present:
            source_columns = [r["name"] for r in sqlite.execute(f'PRAGMA table_info("{table}")')]
            target_rows = pg.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
                (table,),
            ).fetchall()
            target_columns = [r["column_name"] for r in target_rows]
            if not target_columns:
                print(f"SKIP {table}: target table is missing; run the Supabase schema migration first.")
                continue
            columns = [name for name in source_columns if name in target_columns]
            count = sqlite.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            plans.append((table, columns, count))
            print(f"{'PLAN' if not args.apply else 'COPY'} {table}: {count} source rows; columns={len(columns)}")

        if not args.apply:
            print("\nDRY RUN ONLY. No PostgreSQL rows were changed. Re-run with --apply to copy.")
            return

        for table, columns, source_count in plans:
            if not columns:
                print(f"SKIP {table}: no shared columns")
                continue
            quoted_table = sql.Identifier("public", table)
            quoted_columns = sql.SQL(", ").join(sql.Identifier(c) for c in columns)
            placeholders = sql.SQL(", ").join(sql.Placeholder() for _ in columns)
            statement = sql.SQL("INSERT INTO {} ({}) VALUES ({}) ON CONFLICT DO NOTHING").format(
                quoted_table, quoted_columns, placeholders
            )
            offset = 0
            inserted_total = 0
            while offset < source_count:
                rows = sqlite.execute(
                    f'SELECT {", ".join(chr(34) + c + chr(34) for c in columns)} '
                    f'FROM "{table}" LIMIT ? OFFSET ?',
                    (args.batch_size, offset),
                ).fetchall()
                if not rows:
                    break
                before = pg.execute(sql.SQL("SELECT COUNT(*) AS n FROM {}").format(quoted_table)).fetchone()["n"]
                pg.executemany(statement, [tuple(row[c] for c in columns) for row in rows])
                after = pg.execute(sql.SQL("SELECT COUNT(*) AS n FROM {}").format(quoted_table)).fetchone()["n"]
                inserted_total += max(0, after - before)
                offset += len(rows)
            print(f"DONE {table}: source={source_count}, inserted~={inserted_total} (conflicts may be skipped)")

            # Preserve integer IDs and advance serial/identity sequences where applicable.
            if "id" in columns:
                pg.execute(
                    "SELECT setval(pg_get_serial_sequence(%s, 'id'), "
                    "GREATEST(COALESCE((SELECT MAX(id) FROM " + table + "), 1), 1), "
                    "EXISTS(SELECT 1 FROM " + table + "))",
                    ("public." + table,),
                )
        print("\nMigration copy completed. Verify row counts and application behavior before switching traffic.")
        print("No source rows were deleted. The script does not guarantee a full backup; retain the SQLite file.")


if __name__ == "__main__":
    main()
