#!/usr/bin/env python3
"""Convert a not_found.txt file to CSV format for re-processing with rymlist2ytm.py."""

import csv
import sys


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <not_found.txt> [output.csv]")
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else input_path.replace(".txt", ".csv")

    entries = []
    with open(input_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if " - " in line:
                artist, title = line.split(" - ", 1)
                entries.append((artist.strip(), title.strip()))

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for e in entries:
        key = (e[0].lower(), e[1].lower())
        if key not in seen:
            seen.add(key)
            unique.append(e)

    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Rank", "Artist", "Album/Single", "Year", "Format", "Genre"])
        for i, (artist, title) in enumerate(unique, 1):
            writer.writerow([i, artist, title, "", "", ""])

    print(f"Converted {len(unique)} entries → {output_path}")


if __name__ == "__main__":
    main()
