#!/usr/bin/env python3
"""Search YouTube Music for releases from a RateYourMusic CSV and build a playlist."""

import argparse
import csv
import hashlib
import json
import math
import os
import re
import sys
import time
import unicodedata

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
    parser.add_argument("--skip", type=int, default=0, help="Skip the first N entries (manual resume)")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between API calls (default: 1.0)")
    parser.add_argument("--auth", default="browser.json", help="Path to auth file (default: browser.json)")
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


def _normalize(s):
    """Lowercase, strip diacritics and punctuation for loose comparison."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def _artist_matches(query_artist, result):
    """Return True if the query artist matches any artist field in result."""
    q = _normalize(query_artist)
    if not q:
        return False
    candidates = []
    artists = result.get("artists") or []
    for a in artists:
        name = a.get("name", "") if isinstance(a, dict) else str(a)
        if name:
            candidates.append(name)
    author = result.get("author")
    if author:
        candidates.append(author.get("name", "") if isinstance(author, dict) else str(author))
    for c in candidates:
        r = _normalize(c)
        if not r:
            continue
        if q == r or q in r or r in q:
            return True
    return False


def _title_matches(query_title, result):
    """Return True if the result's title has meaningful overlap with query title."""
    r_title = result.get("title", "")
    q = _normalize(query_title)
    r = _normalize(r_title)
    if not q or not r:
        return False
    if q == r or q in r or r in q:
        return True
    stopwords = {"the", "a", "an", "and", "or", "of", "in", "to", "for", "with",
                 "ep", "lp", "vol", "part", "1", "2", "3", "4", "i", "ii", "iii"}
    q_tokens = set(q.split()) - stopwords
    r_tokens = set(r.split()) - stopwords
    if not q_tokens:
        return True
    shared = q_tokens & r_tokens
    return any(len(t) >= 3 for t in shared)


def search_ytm_full(yt, artist, title, delay):
    """Find all track videoIds for a release. Returns list (may be empty).

    Tries album search first (full EP/LP track listing), falls back to
    single song/video search for mixes, unreleased stuff, etc. Validates
    artist match to avoid pulling in unrelated releases.
    """
    variants = split_slash_title(title)

    # Phase 1: Album search - get all tracks from matched album
    for variant in variants:
        query = f"{artist} {variant}"
        album_results = _search_with_retry(yt, query, filter_type="albums", delay=delay)
        for album in album_results[:5]:
            if not _artist_matches(artist, album):
                continue
            if not _title_matches(variant, album):
                continue
            browse_id = album.get("browseId")
            if not browse_id:
                continue
            video_ids = _get_album_tracks(yt, browse_id)
            if video_ids:
                return video_ids

    # Phase 2: Single-song fallback (mixes, singles, unreleased)
    for variant in variants:
        query = f"{artist} {variant}"
        results = _search_with_retry(yt, query, filter_type="songs", delay=delay)
        for item in results:
            if not _artist_matches(artist, item):
                continue
            if not _title_matches(variant, item):
                continue
            vid = item.get("videoId")
            if vid:
                return [vid]

    # Phase 3: Unfiltered fallback (uploads, videos)
    for variant in variants:
        query = f"{artist} {variant}"
        results = _search_with_retry(yt, query, filter_type=None, delay=delay)
        for item in results:
            if not _artist_matches(artist, item):
                continue
            if not _title_matches(variant, item):
                continue
            vid = item.get("videoId")
            if vid:
                return [vid]

    return []


def _get_album_tracks(yt, browse_id):
    """Fetch all track videoIds from an album browseId. Returns list (may be empty)."""
    for attempt in range(MAX_RETRIES):
        try:
            album = yt.get_album(browse_id)
            tracks = album.get("tracks", []) or []
            return [t["videoId"] for t in tracks if t.get("videoId")]
        except Exception:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * (2 ** attempt))
                continue
            return []
    return []


def _search_with_retry(yt, query, filter_type, delay):
    """Execute a single search with retry logic. Returns the raw results list (may be empty)."""
    time.sleep(delay)
    for attempt in range(MAX_RETRIES):
        try:
            if filter_type:
                return yt.search(query, filter=filter_type, limit=5) or []
            return yt.search(query, limit=5) or []
        except Exception:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * (2 ** attempt))
                continue
            return []
    return []


def _create_ytmusic(auth_path):
    """Create a YTMusic instance with a fresh SAPISIDHASH."""
    _ensure_auth_header(auth_path)
    try:
        return YTMusic(auth=auth_path)
    except Exception as e:
        print(f"Error: Authentication failed: {e}", file=sys.stderr)
        sys.exit(1)


def _ensure_auth_header(auth_path):
    """Generate a fresh SAPISIDHASH authorization header from cookies."""
    with open(auth_path, "r") as f:
        data = json.load(f)

    cookie = data.get("cookie", "")
    sapisid = None
    for part in cookie.split(";"):
        part = part.strip()
        if part.startswith("SAPISID="):
            sapisid = part.split("=", 1)[1]
            break

    if not sapisid:
        return  # can't generate without SAPISID

    origin = "https://music.youtube.com"
    timestamp = math.floor(time.time())
    hash_input = f"{timestamp} {sapisid} {origin}"
    sha1 = hashlib.sha1(hash_input.encode("utf-8")).hexdigest()
    data["authorization"] = f"SAPISIDHASH {timestamp}_{sha1}"

    with open(auth_path, "w") as f:
        json.dump(data, f, indent=4)


def main():
    args = parse_args()

    # Check auth file
    if not os.path.exists(args.auth):
        print(f"Error: Auth file not found: {args.auth}", file=sys.stderr)
        print(f"\nCreate browser.json with your YouTube Music cookies.", file=sys.stderr)
        print(f"See README for instructions.", file=sys.stderr)
        sys.exit(1)

    # Ensure browser.json has the authorization header (generate SAPISIDHASH from cookies)
    _ensure_auth_header(args.auth)

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
    yt = _create_ytmusic(args.auth)

    # Create or resume playlist
    skip_count = args.skip
    playlist_id = args.playlist_id

    if args.resume and not skip_count:
        if not playlist_id:
            print("Error: --resume requires --playlist-id", file=sys.stderr)
            sys.exit(1)
        try:
            playlist_info = yt.get_playlist(playlist_id, limit=0)
            skip_count = playlist_info.get("trackCount", 0)
            print(f"Resuming playlist: {playlist_info.get('title', playlist_id)}")
            print(f"Tracks already in playlist: {skip_count}")
        except Exception as e:
            print(f"Warning: Could not read playlist to auto-resume: {e}", file=sys.stderr)
            print("Use --skip N to manually skip entries.", file=sys.stderr)
            sys.exit(1)

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
    nf_mode = "a" if (args.resume or args.skip) else "w"
    notfound_file = open(args.not_found, nf_mode, encoding="utf-8")

    width = len(str(total))
    entries_added = 0
    tracks_added = 0
    notfound_count = 0
    auth_fail_count = 0
    consecutive_401s = 0
    MAX_CONSECUTIVE_401S = 3
    last_processed = skip_count
    playlist_part = 1
    all_playlist_ids = [playlist_id]

    print(f"RYMList2YTM - YouTube Music Playlist Builder")
    print(f"=============================================")
    print(f"Input: {args.input} ({total} entries)")
    if skip_count:
        print(f"Skipping first {skip_count} entries")
    print()

    auth_expired = False
    try:
        refresh_interval = 50
        entries_since_refresh = 0

        for i, (artist, title) in enumerate(entries, 1):
            if i <= skip_count:
                continue

            entries_since_refresh += 1
            if entries_since_refresh >= refresh_interval:
                yt = _create_ytmusic(args.auth)
                entries_since_refresh = 0

            key = f"{artist} - {title}"
            label = f"[{i:>{width}}/{total}] {key}"
            print(f"{label} ... ", end="", flush=True)

            video_ids = search_ytm_full(yt, artist, title, args.delay)

            if video_ids:
                added = False
                for add_attempt in range(2):
                    try:
                        yt.add_playlist_items(playlist_id, videoIds=video_ids, duplicates=True)
                        print(f"\u2713 added {len(video_ids)} track(s)")
                        entries_added += 1
                        tracks_added += len(video_ids)
                        added = True
                        consecutive_401s = 0
                        time.sleep(args.delay)
                        break
                    except Exception as e:
                        err = str(e)
                        if "Maximum playlist size" in err:
                            playlist_part += 1
                            new_name = f"{args.playlist_name} Part {playlist_part}"
                            print(f"\n>>> Playlist full! Creating: {new_name}")
                            try:
                                playlist_id = yt.create_playlist(
                                    title=new_name,
                                    description=f"Auto-generated from {args.input} by RYMList2FLAC (Part {playlist_part})",
                                    privacy_status="PRIVATE",
                                )
                                all_playlist_ids.append(playlist_id)
                                print(f">>> New playlist ID: {playlist_id}\n")
                                yt.add_playlist_items(playlist_id, videoIds=video_ids, duplicates=True)
                                print(f"[{i:>{width}}/{total}] {key} ... \u2713 added {len(video_ids)} track(s)")
                                entries_added += 1
                                tracks_added += len(video_ids)
                                added = True
                                consecutive_401s = 0
                                time.sleep(args.delay)
                            except Exception as e2:
                                print(f"\u2717 failed to create continuation playlist ({e2})")
                            break
                        if "401" in err and add_attempt == 0:
                            yt = _create_ytmusic(args.auth)
                            continue
                        if "401" in err:
                            print("\u2717 auth expired")
                            auth_fail_count += 1
                            consecutive_401s += 1
                        else:
                            print(f"\u2717 add failed ({e})")
                            notfound_file.write(key + "\n")
                            notfound_file.flush()
                            notfound_count += 1
                            consecutive_401s = 0
                if not added and consecutive_401s >= MAX_CONSECUTIVE_401S:
                    auth_expired = True
                    last_processed = i - consecutive_401s
                    print(f"\n>>> Session cookies expired ({consecutive_401s} consecutive 401s).")
                    print(f">>> Last successful entry: {last_processed}")
                    print(f">>> Stopping to avoid wasting entries.\n")
                    break
            else:
                print("\u2717 not found")
                notfound_file.write(key + "\n")
                notfound_file.flush()
                notfound_count += 1
                consecutive_401s = 0

            last_processed = i

    except KeyboardInterrupt:
        print("\n\nInterrupted! Progress saved to playlist.")
    finally:
        notfound_file.close()

    print()
    print(f"=============================================")
    print(f"Done! Entries: {entries_added} | Tracks: {tracks_added} | Not found: {notfound_count} | Auth failed: {auth_fail_count} | Skipped: {skip_count}")
    for pid in all_playlist_ids:
        print(f"Playlist ID: {pid}")
    print(f"Not found written to: {args.not_found}")
    if auth_expired or auth_fail_count > 0:
        print(f"\nTo resume after refreshing browser.json cookies:")
        print(f"  python3 {sys.argv[0]} --skip {last_processed} --playlist-id {playlist_id} --input {args.input} --not-found {args.not_found}")


if __name__ == "__main__":
    main()
