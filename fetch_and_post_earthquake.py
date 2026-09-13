from urllib.parse import quote
import argparse
import json
import os
import sys
import tempfile
import time
import traceback
import re
from datetime import datetime, timedelta, timezone
from math import radians, degrees, sin, cos, sqrt, atan2
import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path='.env.local')

usgs_api_url = os.getenv('USGS_API_URL')
latitude = os.getenv('LATITUDE')
longitude = os.getenv('LONGITUDE')
max_radius = os.getenv('MAX_RADIUS_KM')

# Multi-point reference grid: the tier-qualifying distance for an event is the
# MINIMUM haversine distance from its epicenter to any of these anchors.
# The env LATITUDE/LONGITUDE above are left intact (USGS API params).
REFERENCE_ANCHORS = [
    ('san francisco', 37.77, -122.42),
    ('oakland', 37.80, -122.27),
    ('san jose', 37.34, -121.89),
]

_COMPASS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
            'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']


def bearing_dir(lat1, lon1, lat2, lon2):
    # 16-point compass direction from (lat1, lon1) toward (lat2, lon2).
    p1, p2 = radians(lat1), radians(lat2)
    dl = radians(lon2 - lon1)
    y = sin(dl) * cos(p2)
    x = cos(p1) * sin(p2) - sin(p1) * cos(p2) * cos(dl)
    bearing = (degrees(atan2(y, x)) + 360.0) % 360.0
    return _COMPASS[int((bearing + 11.25) // 22.5) % 16]


def closest_reference(lat, lon):
    # Nearest reference anchor: (Display Name, miles, compass direction).
    # direction is '' when the epicenter is within 1 mile of the anchor.
    best = None
    for name, alat, alon in REFERENCE_ANCHORS:
        d = calculate_distance(alat, alon, lat, lon)
        if best is None or d < best[1]:
            best = (name, d, '' if d < 1 else bearing_dir(alat, alon, lat, lon))
    return best[0].title(), best[1], best[2]

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'earthquake_state.json')
STATE_MAX_AGE = timedelta(hours=24)
DRY_RUN = False


def load_state():
    """Return the state dict, or None if the file does not exist yet.

    A corrupt file is treated as empty (bootstrap happens on that run)
    rather than crashing every 5 minutes.
    """
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except FileNotFoundError:
        return None
    except (ValueError, OSError) as e:
        print(f"WARNING: could not read state file {STATE_FILE} ({e}); starting with empty state")
        return {'seen': {}}
    if not isinstance(state, dict) or not isinstance(state.get('seen'), dict):
        print(f"WARNING: state file {STATE_FILE} has unexpected shape; starting with empty state")
        return {'seen': {}}
    return state


def save_state(state):
    """Atomically write the state file: temp file in same dir, fsync, replace. Mode 600."""
    d = os.path.dirname(STATE_FILE)
    fd, tmp_path = tempfile.mkstemp(dir=d, prefix='earthquake_state.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(state, f, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
            os.fchmod(f.fileno(), 0o600)
        os.replace(tmp_path, STATE_FILE)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def prune_seen(seen, now):
    """Drop seen entries older than STATE_MAX_AGE. Returns number pruned."""
    pruned = 0
    for fid in list(seen.keys()):
        ts = seen[fid]
        try:
            stored = datetime.fromisoformat(ts)
            if stored.tzinfo is None:
                stored = stored.replace(tzinfo=timezone.utc)
            if now - stored > STATE_MAX_AGE:
                del seen[fid]
                pruned += 1
        except (ValueError, TypeError):
            print(f"WARNING: unparseable timestamp for seen id {fid}; keeping it")
    return pruned


def fetch_new_earthquakes():
    """Fetch recent USGS earthquakes within the tiering filter.

    Returns a list of feature dicts, or None on a definitive failure
    (4xx or exhausted retries). Never raises.
    """
    max_attempts = 4
    timeout_s = 4
    sleep_s = 2

    # Get the current time once before the loop
    current_time = datetime.now(timezone.utc)
    params = {
        'format': 'geojson',
        'starttime': (current_time - timedelta(seconds=390)).isoformat(),
        'latitude': latitude,
        'longitude': longitude,
        'maxradiuskm': max_radius
    }

    response = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.get(usgs_api_url, params=params, timeout=timeout_s)
            if 400 <= response.status_code < 500:
                print(f"HTTP {response.status_code} (client error) from USGS; not retrying. "
                      f"Body snippet: {response.text[:200]!r}")
                return None
            response.raise_for_status()
            break
        except requests.RequestException as e:
            if attempt == max_attempts:
                print(f"Error fetching USGS after {max_attempts} attempts: {e}")
                return None
            print(f"Fetch attempt {attempt} failed ({e}). Retrying in {sleep_s}s...")
            time.sleep(sleep_s)

    try:
        data = response.json()
    except ValueError as e:
        print(f"ERROR: USGS response is not valid JSON ({e}). Snippet: {response.text[:200]!r}")
        return None
    if not isinstance(data, dict):
        print(f"ERROR: unexpected USGS response shape (not an object). Snippet: {response.text[:200]!r}")
        return None

    new_earthquakes = []
    for feature in data.get('features', []):
        if feature['properties']['type'] != 'earthquake':
            print(f"found a non-earthquake type: {feature['properties']['type']}")
            continue

        magnitude = feature['properties']['mag']
        if magnitude is None:
            print(f"Skipping event with null magnitude: id={feature.get('id')}, "
                  f"place={feature['properties'].get('place')}")
            continue

        # Tier-qualifying distance: miles from the epicenter to the CLOSEST
        # reference anchor (min over the grid).
        eq_lat = feature['geometry']['coordinates'][1]
        eq_lon = feature['geometry']['coordinates'][0]
        _, distance, _ = closest_reference(eq_lat, eq_lon)

        # Depth-aware notability: shallow events (depth < 10 km) within 50 mi
        # lower the applicable tier's magnitude threshold by 0.5. Null depth
        # gets no discount (conservative); log that once per run.
        global _NULL_DEPTH_LOGGED
        depth = parse_depth(feature)
        if depth is None and not _NULL_DEPTH_LOGGED:
            print("WARNING: null depth on event; no depth discount applied")
            _NULL_DEPTH_LOGGED = True
        shallow = (depth is not None and depth < 10 and distance <= 50)

        if distance <= 25:
            if magnitude >= (2.0 if shallow else 2.5):
                log_earthquake(distance, feature)
                new_earthquakes.append(feature)
            else:
                log_earthquake(distance, feature, "Skipping")
        elif distance <= 50 and magnitude >= (1.5 if shallow else 2.0):
            log_earthquake(distance, feature)
            new_earthquakes.append(feature)
        elif distance <= 100 and magnitude >= 4.0:
            log_earthquake(distance, feature)
            new_earthquakes.append(feature)
        elif distance <= 200 and magnitude >= 5.0:
            log_earthquake(distance, feature)
            new_earthquakes.append(feature)
        elif distance > 200 and magnitude >= 7.0:
            log_earthquake(distance, feature)
            new_earthquakes.append(feature)
        else:
            log_earthquake(distance, feature, "Skipping")

    return new_earthquakes


def parse_depth(feature):
    """Return the USGS depth (km) as float, or None if absent/unparseable."""
    depth = (feature.get('properties') or {}).get('depth')
    if depth is None:
        return None
    try:
        return float(depth)
    except (TypeError, ValueError):
        return None


_NULL_DEPTH_LOGGED = False


def build_post_message(earthquake):
    """Build the Threads post text (and maps link) for an earthquake feature."""
    magnitude = round(earthquake['properties']['mag'], 1)
    coordinates = earthquake['geometry']['coordinates']
    lat, lon = coordinates[1], coordinates[0]  # USGS returns [lon, lat]

    # Caption location clause: closest reference anchor only (no depth text).
    city, miles, direction = closest_reference(lat, lon)
    if miles < 1:
        location = f"near {city}"
    else:
        location = f"{miles:.1f} miles {direction} of {city}"

    google_maps_link = f"https://www.google.com/maps/place/{lat}+{lon}/@{lat},{lon},9z"
    usgs_link = earthquake['properties']['url']

    if magnitude < 3.0:
        prefix = "\U0001F634"
    elif 3.0 <= magnitude < 4.0:
        prefix = "\U0001F633"
    elif 4.0 <= magnitude < 5.0:
        prefix = "\U0001FAE8"
    elif 5.0 <= magnitude < 7.0:
        prefix = "\U0001FAE8\ufe0f\U0001FAE8\ufe0f"
    else:
        prefix = "\U0001FAE8\ufe0f\U0001FAE8\U0001FAE8\U0001FAE8"

    _occ_dt = datetime.fromtimestamp(
        earthquake["properties"]["time"] / 1000, tz=timezone.utc
    ).astimezone()
    occurred_time = f"{_occ_dt.strftime('%I').lstrip('0')}:{_occ_dt.strftime('%M')} {_occ_dt.strftime('%p').lower()}"

    post_message = f"{prefix} {magnitude} magnitude earthquake occurred {location} at {occurred_time}."
    details_message = f" Details: {usgs_link}"

    if len(post_message) + len(details_message) <= 500:
        post_message += details_message

    return post_message, google_maps_link


def post_single_to_threads(post_message, google_maps_link):
    """Post one earthquake to Threads. Returns True only on full success.

    Uses Authorization: Bearer header (token no longer in the URL query).
    Retries once on failure before giving up.
    """
    THREADS_USER_ID = os.getenv('THREADS_USER_ID')
    THREADS_ACCESS_TOKEN = os.getenv('THREADS_ACCESS_TOKEN')
    if not THREADS_USER_ID or not THREADS_ACCESS_TOKEN:
        print("ERROR: THREADS_USER_ID or THREADS_ACCESS_TOKEN not set; cannot post.")
        return False

    THREADS_API_URL = (
        f"https://graph.threads.net/{THREADS_USER_ID}/threads"
        f"?text={quote(post_message)}&media_type=TEXT&link_attachment={quote(google_maps_link)}"
    )
    headers = {'Authorization': f'Bearer {THREADS_ACCESS_TOKEN}'}

    for attempt in (1, 2):
        try:
            response = requests.post(THREADS_API_URL, headers=headers, timeout=4)
            response.raise_for_status()

            data = response.json()
            creation_id = data.get('id') if isinstance(data, dict) else None
            if not creation_id:
                print(f"ERROR: Threads creation response missing 'id': {response.text[:200]!r}")
                break

            publish_url = (
                f"https://graph.threads.net/{THREADS_USER_ID}/threads_publish"
                f"?creation_id={creation_id}"
            )
            publish_response = requests.post(publish_url, headers=headers, timeout=4)
            publish_response.raise_for_status()

            print("Earthquake posted successfully.")
            return True
        except requests.RequestException as e:
            print(f"Failed to post earthquake (attempt {attempt}): {e}")
            if attempt == 1:
                time.sleep(2)
    return False


def calculate_distance(lat1, lon1, lat2, lon2):
    R = 3959

    lat1, lon1, lat2, lon2 = map(radians, [float(lat1), float(lon1), float(lat2), float(lon2)])

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    distance = R * c

    return distance


def log_earthquake(distance, feature, action="Found"):
    magnitude = feature['properties']['mag']
    depth = parse_depth(feature)
    depth_part = f"{round(depth)} km" if depth is not None else "depth n/a"
    occurred_time = datetime.fromtimestamp(feature['properties']['time'] / 1000).strftime('%Y-%m-%d %H:%M:%S')
    updated_time = datetime.fromtimestamp(feature['properties']['updated'] / 1000).strftime('%Y-%m-%d %H:%M:%S')

    status = "Skipping" if action == "Skipping" else "Found"
    print(f"{status} earthquake: magnitude {magnitude}, distance {distance:.1f} miles (nearest anchor), depth {depth_part}, occurred: {occurred_time}, updated: {updated_time}")


def main():
    global DRY_RUN
    ap = argparse.ArgumentParser(description="Fetch recent BA-area earthquakes and post new ones to Threads.")
    ap.add_argument('--dry-run', action='store_true',
                    help="Do everything except the Threads POST calls; do not record posts as seen")
    args = ap.parse_args()
    DRY_RUN = args.dry_run
    if DRY_RUN:
        print("=== DRY RUN: no posts to Threads will be made ===")

    now = datetime.now(timezone.utc)

    state = load_state()
    earthquakes = fetch_new_earthquakes()
    if earthquakes is None:
        print("Fetch failed; skipping this run. State file unchanged.")
        return

    # First-run bootstrap: mark everything currently in the window as seen WITHOUT posting.
    if state is None:
        state = {'seen': {}}
        for eq in earthquakes:
            fid = eq.get('id')
            if fid:
                state['seen'][fid] = now.isoformat()
        save_state(state)
        print(f"BOOTSTRAP: state file did not exist; initialized with {len(state['seen'])} "
              f"current earthquake ids (NOT posted)")
        print("No earthquakes posted (bootstrap run).")
        return

    pruned = prune_seen(state['seen'], now)
    if pruned:
        print(f"Pruned {pruned} seen entries older than 24h.")

    seen = state['seen']
    to_post = [eq for eq in earthquakes if eq.get('id') and eq['id'] not in seen]

    if not to_post:
        print("No new earthquakes found")
        return

    if DRY_RUN:
        for eq in to_post:
            post_message, _ = build_post_message(eq)
            print(f"[DRY-RUN] WOULD POST: {post_message}")
        print(f"[DRY-RUN] {len(to_post)} earthquake(s) would be posted; no posts made, seen-set not updated for them.")
        return

    posted_ids = []
    for eq in to_post:
        post_message, google_maps_link = build_post_message(eq)
        print(post_message)
        if post_single_to_threads(post_message, google_maps_link):
            posted_ids.append(eq['id'])

    if posted_ids:
        for fid in posted_ids:
            seen[fid] = now.isoformat()
        save_state(state)
        print(f"State updated; now tracking {len(seen)} seen ids.")
    else:
        print("No earthquakes posted successfully; state file not updated (will retry next run).")


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print("UNEXPECTED ERROR:")
        traceback.print_exc()
        sys.exit(0)
