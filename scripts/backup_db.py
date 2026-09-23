"""Consistent SQLite backup, including committed WAL changes. Never overwrite a backup."""
import argparse
import sqlite3
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("source")
parser.add_argument("destination")
args = parser.parse_args()
source = Path(args.source).resolve(strict=True)
destination = Path(args.destination).resolve()
if destination.exists() or source == destination:
    parser.error("La destinazione esiste già: scegli un nuovo nome per il backup.")
destination.parent.mkdir(parents=True, exist_ok=True)
src = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
dst = sqlite3.connect(destination)
try:
    src.backup(dst)
finally:
    dst.close()
    src.close()
print(f"Backup creato: {destination}")
