from urllib.parse import quote
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import os
import time
import re  # Import the regular expression module

load_dotenv(dotenv_path='.env.local')

usgs_api_url = os.getenv('USGS_API_URL')
latitude = os.getenv('LATITUDE')
longitude = os.getenv('LONGITUDE')
max_radius = os.getenv('MAX_RADIUS_KM')

def fetch_new_earthquakes():
    url = usgs_api_url
    max_attempts = 5  
    attempt = 0

    # Get the current time once before the loop
    current_time = datetime.now(timezone.utc)

    while attempt < max_attempts:
        try:
            params = {
                'format': 'geojson',
                'updatedafter': (current_time - timedelta(minutes=5)).isoformat(),  
                'latitude': latitude,  
                'longitude': longitude,  
                'maxradiuskm': max_radius 
            }

            response = requests.get(url, params=params)
            response.raise_for_status()
            
            data = response.json()
            new_earthquakes = []
            
            for feature in data['features']:
                if feature['properties']['type'] != 'earthquake':
                    print(f"found a non-earthquake type: {feature['properties']['type']}")
                    continue

                magnitude = feature['properties']['mag']
                distance_km = feature['properties']['distance']  

                # add all earthquakes under 100 mile radius 
                if distance_km <= 161:
                    print(f"Found earthquake: magnitude {magnitude}, distance {distance_km:.1f} km, occurred: {datetime.fromtimestamp(feature['properties']['time'] / 1000).strftime('%Y-%m-%d %H:%M:%S')}, updated: {datetime.fromtimestamp(feature['properties']['updated'] / 1000).strftime('%Y-%m-%d %H:%M:%S')}")
                    new_earthquakes.append(feature)
                # only add earthquakes M4.5+ for 100+ mile distance
                elif magnitude >= 4.5:
                    print(f"Found strong distant earthquake: magnitude {magnitude}, distance {distance_km:.1f} km, occurred: {datetime.fromtimestamp(feature['properties']['time'] / 1000).strftime('%Y-%m-%d %H:%M:%S')}, updated: {datetime.fromtimestamp(feature['properties']['updated'] / 1000).strftime('%Y-%m-%d %H:%M:%S')}")
                    new_earthquakes.append(feature)
                else:
                    print(f"Skipping earthquake: magnitude {magnitude}, distance {distance_km:.1f} km, occurred: {datetime.fromtimestamp(feature['properties']['time'] / 1000).strftime('%Y-%m-%d %H:%M:%S')}, updated: {datetime.fromtimestamp(feature['properties']['updated'] / 1000).strftime('%Y-%m-%d %H:%M:%S')}")

            return new_earthquakes
        
        except requests.RequestException as e:
            print(f"Attempt {attempt + 1} failed: {e}")
            attempt += 1  
            if attempt < max_attempts:  
                time.sleep(1)  
            if attempt == max_attempts:
                print("Max attempts reached. Giving up.")
                return []

def post_to_threads(earthquakes):
    THREADS_USER_ID = os.getenv('THREADS_USER_ID')
    THREADS_ACCESS_TOKEN = os.getenv('THREADS_ACCESS_TOKEN')
    
    for earthquake in earthquakes:  
        magnitude = round(earthquake['properties']['mag'], 1)  
        location = earthquake['properties']['place']
        coordinates = earthquake['geometry']['coordinates']
        lat, lon = coordinates[1], coordinates[0]  # USGS returns [lon, lat]
        
        # Convert km to mi
        match = re.match(r"(\d+)\s*km", location)
        if match:
            km_value = float(match.group(1))
            miles_value = km_value * 0.621371  # Convert km to miles
            miles_value = round(miles_value, 1)  # Round to one decimal point
            
            mile_string = "mile" if miles_value < 1 else "miles"
            location = f"{miles_value} {mile_string} {location[match.end():].strip()}"  # Replace km with miles

        google_maps_link = f"https://www.google.com/maps/place/{lat}+{lon}/@{lat},{lon},9z"
        usgs_link = earthquake['properties']['url']

        if magnitude < 3.0:
            prefix = "😴"
        elif 3.0 <= magnitude < 4.0:
            prefix = "😳"
        elif 4.0 <= magnitude < 5.0:
            prefix = "🫨"
        elif 5.0 <= magnitude < 7.0:
            prefix = "🫨️🫨️"
        else: 
            prefix = "🫨️🫨🫨🫨"

        post_message = f"{prefix} {magnitude} magnitude earthquake occurred {location}."
        details_message = f" Details: {usgs_link}"

        if len(post_message) + len(details_message) <= 500:
            post_message += details_message

        print(post_message)

        THREADS_API_URL = (
            f"https://graph.threads.net/{THREADS_USER_ID}/threads?text={quote(post_message)}"
            f"&access_token={THREADS_ACCESS_TOKEN}&media_type=TEXT&link_attachment={quote(google_maps_link)}"
        )
        
        try:
            response = requests.post(THREADS_API_URL)
            response.raise_for_status()
            
            data = response.json()
            creation_id = data.get('id')  
            
            publish_url = f"https://graph.threads.net/{THREADS_USER_ID}/threads_publish?creation_id={creation_id}&access_token={THREADS_ACCESS_TOKEN}"
            publish_response = requests.post(publish_url)
            publish_response.raise_for_status()  
            
            print("Earthquake posted successfully.")
        except requests.RequestException as e:
            print(f"Failed to post earthquake: {e}")

if __name__ == "__main__":
    new_earthquakes = fetch_new_earthquakes()
    if new_earthquakes:
        post_to_threads(new_earthquakes)
    else:
        print("No new earthquakes found")
