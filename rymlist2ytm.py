#!/usr/bin/env python3
"""Search YouTube Music for releases from a RateYourMusic CSV and build a playlist."""

import argparse
import csv
import os
import sys
import time

try:
    from ytmusicapi import YTMusic
except ImportError:
    print("Error: ytmusicapi is required. Install it with: pip install ytmusicapi", file=sys.stderr)
    sys.exit(1)

MAX_RETRIES = 3
RETRY_DELAY = 2.0


def parse_args():
    parser = argparse.ArgumentParser(
        description="Search YouTube Music for releases from a RYM CSV and build a playlist."
    )
    parser.add_argument("--input", default="Get_Hype.csv", help="Input CSV file (default: Get_Hype.csv)")
    parser.add_argument("--playlist-name", default="Get Hype", help="Name for the new playlist (default: Get Hype)")
    parser.add_argument("--playlist-id", default=None, help="Existing playlist ID (for use with --resume)")
    parser.add_argument("--not-found", default="not_found.txt", help="Output file for entries not found (default: not_found.txt)")
    parser.add_argument("--resume", action="store_true", help="Skip entries already in the playlist")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between API calls (default: 1.0)")
    parser.add_argument("--auth", default="oauth.json", help="Path to OAuth token file (default: oauth.json)")
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


def search_ytm(yt, artist, title, delay):
    """Search YouTube Music with fallback cascade. Returns videoId or None."""
    variants = split_slash_title(title)

    # Phase 1: Song-filtered search (official YTM songs only)
    for variant in variants:
        query = f"{artist} {variant}"
        result = _search_with_retry(yt, query, filter_type="songs", delay=delay)
        if result:
            return result

    # Phase 2: Unfiltered search (catches videos, uploads, etc.)
    for variant in variants:
        query = f"{artist} {variant}"
        result = _search_with_retry(yt, query, filter_type=None, delay=delay)
        if result:
            return result

    return None


def _search_with_retry(yt, query, filter_type, delay):
    """Execute a single search with retry logic. Returns videoId or None."""
    time.sleep(delay)
    for attempt in range(MAX_RETRIES):
        try:
            if filter_type:
                results = yt.search(query, filter=filter_type, limit=5)
            else:
                results = yt.search(query, limit=5)

            for item in results:
                vid = item.get("videoId")
                if vid:
                    return vid
            return None
        except Exception:
            if attempt < MAX_RETRIES - 1:
                wait = RETRY_DELAY * (2 ** attempt)
                time.sleep(wait)
                continue
            return None
    return None


def main():
    args = parse_args()

    # Check auth file
    if not os.path.exists(args.auth):
        print(f"Error: OAuth token file not found: {args.auth}", file=sys.stderr)
        print(f"\nRun this first to authenticate with YouTube Music:", file=sys.stderr)
        print(f"  ytmusicapi oauth", file=sys.stderr)
        print(f"\nThis will open a browser for Google sign-in and save the token to oauth.json", file=sys.stderr)
        sys.exit(1)

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

    # Authenticate
    print("Authenticating with YouTube Music...")
    try:
        yt = YTMusic(auth=args.auth)
    except Exception as e:
        print(f"Error: Authentication failed: {e}", file=sys.stderr)
        print("Try re-running: ytmusicapi oauth", file=sys.stderr)
        sys.exit(1)

    # Create or resume playlist
    skip_count = 0
    playlist_id = args.playlist_id

    if args.resume:
        if not playlist_id:
            print("Error: --resume requires --playlist-id", file=sys.stderr)
            sys.exit(1)
        try:
            playlist_info = yt.get_playlist(playlist_id, limit=0)
            skip_count = playlist_info.get("trackCount", 0)
            print(f"Resuming playlist: {playlist_info.get('title', playlist_id)}")
            print(f"Tracks already in playlist: {skip_count}")
        except Exception as e:
            print(f"Error: Could not read playlist {playlist_id}: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        if playlist_id:
            print(f"Using existing playlist: {playlist_id}")
        else:
            print(f"Creating playlist: {args.playlist_name}")
            try:
                playlist_id = yt.create_playlist(
                    title=args.playlist_name,
                    description=f"Auto-generated from {args.input} by RYMList2FLAC",
                    privacy_status="PRIVATE",
                )
                if not isinstance(playlist_id, str):
                    print(f"Error: Failed to create playlist: {playlist_id}", file=sys.stderr)
                    sys.exit(1)
            except Exception as e:
                print(f"Error: Failed to create playlist: {e}", file=sys.stderr)
                sys.exit(1)

    print(f"Playlist ID: {playlist_id}")
    print(f"(save this ID in case you need to --resume later)\n")

    # Open not-found file
    nf_mode = "a" if args.resume else "w"
    notfound_file = open(args.not_found, nf_mode, encoding="utf-8")

    width = len(str(total))
    added_count = 0
    notfound_count = 0

    print(f"RYMList2YTM - YouTube Music Playlist Builder")
    print(f"=============================================")
    print(f"Input: {args.input} ({total} entries)")
    if skip_count:
        print(f"Skipping first {skip_count} entries (already in playlist)")
    print()

    try:
        for i, (artist, title) in enumerate(entries, 1):
            if i <= skip_count:
                continue

            key = f"{artist} - {title}"
            label = f"[{i:>{width}}/{total}] {key}"
            print(f"{label} ... ", end="", flush=True)

            video_id = search_ytm(yt, artist, title, args.delay)

            if video_id:
                try:
                    yt.add_playlist_items(playlist_id, videoIds=[video_id], duplicates=True)
                    print("\u2713 added")
                    added_count += 1
                    time.sleep(args.delay)
                except Exception as e:
                    print(f"\u2717 add failed ({e})")
                    notfound_file.write(key + "\n")
                    notfound_file.flush()
                    notfound_count += 1
            else:
                print("\u2717 not found")
                notfound_file.write(key + "\n")
                notfound_file.flush()
                notfound_count += 1

    except KeyboardInterrupt:
        print("\n\nInterrupted! Progress saved to playlist.")
        print(f"To resume: python3 {sys.argv[0]} --resume --playlist-id {playlist_id}")
    finally:
        notfound_file.close()

    print()
    print(f"=============================================")
    print(f"Done! Added: {added_count} | Not found: {notfound_count} | Skipped: {skip_count}")
    print(f"Playlist ID: {playlist_id}")
    print(f"Not found written to: {args.not_found}")


if __name__ == "__main__":
    main()
