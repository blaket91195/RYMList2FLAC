#!/usr/bin/env python3
"""Search Deezer for releases from a RateYourMusic CSV export and output URLs for Deemix."""

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DEEZER_ALBUM_SEARCH = "https://api.deezer.com/search/album"
DEEZER_TRACK_SEARCH = "https://api.deezer.com/search/track"
DEEZER_ALBUM_URL = "https://www.deezer.com/album/{}"
MAX_RETRIES = 3
RETRY_DELAY = 2.0


def parse_args():
    parser = argparse.ArgumentParser(
        description="Search Deezer for releases from a RYM CSV and output URLs for Deemix."
    )
    parser.add_argument("--input", default="Get_Hype.csv", help="Input CSV file (default: Get_Hype.csv)")
    parser.add_argument("--output", default="deezer_urls.txt", help="Output file for Deezer URLs (default: deezer_urls.txt)")
    parser.add_argument("--not-found", default="not_found.txt", help="Output file for entries not found (default: not_found.txt)")
    parser.add_argument("--resume", action="store_true", help="Skip already-processed entries from a previous run")
    parser.add_argument("--delay", type=float, default=0.3, help="Seconds between API calls (default: 0.3)")
    return parser.parse_args()


def read_csv(filepath):
    """Read the CSV and return a list of (artist, title) tuples, skipping empty entries."""
    entries = []
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            artist = row.get("Artist", "").strip()
            title = row.get("Album/Single", "").strip()
            if artist and title:
                entries.append((artist, title))
    return entries


def split_slash_title(title):
    """Return [full_title] or [full_title, first_part] if title contains ' / '."""
    if " / " in title:
        first_part = title.split(" / ", 1)[0].strip()
        return [title, first_part]
    return [title]


def deezer_request(url, params, delay):
    """Make a GET request to the Deezer API with retry logic."""
    full_url = url + "?" + urllib.parse.urlencode(params)
    for attempt in range(MAX_RETRIES):
        time.sleep(delay)
        try:
            req = urllib.request.Request(full_url, headers={"User-Agent": "RYMList2FLAC/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                wait = RETRY_DELAY * (2 ** attempt)
                print(f"  [retry in {wait:.0f}s - HTTP {e.code}]", file=sys.stderr)
                time.sleep(wait)
                continue
            return None
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            wait = RETRY_DELAY * (2 ** attempt)
            time.sleep(wait)
            continue
    return None


def search_album(artist, title, delay):
    """Search Deezer album endpoint. Returns album URL or None."""
    query = f'artist:"{artist}" album:"{title}"'
    data = deezer_request(DEEZER_ALBUM_SEARCH, {"q": query}, delay)
    if data and data.get("data"):
        album_id = data["data"][0].get("id")
        if album_id:
            return DEEZER_ALBUM_URL.format(album_id)
    return None


def search_track(artist, title, delay):
    """Search Deezer track endpoint and return the parent album URL, or None."""
    query = f'artist:"{artist}" track:"{title}"'
    data = deezer_request(DEEZER_TRACK_SEARCH, {"q": query}, delay)
    if data and data.get("data"):
        try:
            album_id = data["data"][0]["album"]["id"]
            return DEEZER_ALBUM_URL.format(album_id)
        except (KeyError, IndexError, TypeError):
            pass
    return None


def find_deezer_url(artist, title, delay):
    """Try the search cascade: album (full, short) then track (full, short)."""
    variants = split_slash_title(title)

    # Try album search with all title variants first
    for variant in variants:
        url = search_album(artist, variant, delay)
        if url:
            return url

    # Fall back to track search with all title variants
    for variant in variants:
        url = search_track(artist, variant, delay)
        if url:
            return url

    return None


def load_resume_state(progress_path):
    """Load the set of already-processed entry keys from the progress log."""
    processed = set()
    try:
        with open(progress_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "\t" in line:
                    _, key = line.split("\t", 1)
                    processed.add(key.lower())
    except FileNotFoundError:
        pass
    return processed


def main():
    args = parse_args()

    # Read input CSV
    try:
        entries = read_csv(args.input)
    except FileNotFoundError:
        print(f"Error: CSV file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    total = len(entries)
    if total == 0:
        print("No entries found in CSV.")
        sys.exit(0)

    # Resume state - keep progress log alongside output file
    output_dir = os.path.dirname(os.path.abspath(args.output))
    progress_path = os.path.join(output_dir, "_progress.log")
    processed = set()
    skipped = 0
    if args.resume:
        processed = load_resume_state(progress_path)

    # Open output files
    mode = "a" if args.resume else "w"
    urls_file = open(args.output, mode, encoding="utf-8")
    notfound_file = open(args.not_found, mode, encoding="utf-8")
    progress_file = open(progress_path, mode, encoding="utf-8")

    width = len(str(total))
    found_count = 0
    notfound_count = 0

    print(f"RYMList2FLAC - Deezer URL Finder")
    print(f"=================================")
    print(f"Input: {args.input} ({total} entries)")
    print(f"Output: {args.output}, {args.not_found}")
    if args.resume and processed:
        print(f"Resume: skipping {len(processed)} already processed")
    print()

    try:
        for i, (artist, title) in enumerate(entries, 1):
            key = f"{artist} - {title}"

            if args.resume and key.lower() in processed:
                skipped += 1
                continue

            label = f"[{i:>{width}}/{total}] {key}"
            print(f"{label} ... ", end="", flush=True)

            url = find_deezer_url(artist, title, args.delay)

            if url:
                print("\u2713 found")
                urls_file.write(url + "\n")
                urls_file.flush()
                progress_file.write(f"FOUND\t{key}\n")
                progress_file.flush()
                found_count += 1
            else:
                print("\u2717 not found")
                notfound_file.write(key + "\n")
                notfound_file.flush()
                progress_file.write(f"NOTFOUND\t{key}\n")
                progress_file.flush()
                notfound_count += 1

    except KeyboardInterrupt:
        print("\n\nInterrupted! Progress saved.")
    finally:
        urls_file.close()
        notfound_file.close()
        progress_file.close()

    print()
    print(f"=================================")
    print(f"Done! Found: {found_count} | Not found: {notfound_count} | Skipped: {skipped}")
    print(f"URLs written to: {args.output}")
    print(f"Not found written to: {args.not_found}")


if __name__ == "__main__":
    main()
