from urllib.parse import quote
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import os

load_dotenv(dotenv_path='.env.local')

usgs_api_url = os.getenv('USGS_API_URL')
latitude = os.getenv('LATITUDE')
longitude = os.getenv('LONGITUDE')
max_radius = os.getenv('MAX_RADIUS')

def fetch_new_earthquakes():
    url = usgs_api_url
    
    params = {
        'format': 'geojson',
        'starttime': (datetime.now(timezone.utc) - timedelta(minutes=5000, seconds=3)).isoformat(),
        'endtime': datetime.now(timezone.utc).isoformat(),
        'latitude': latitude,  
        'longitude': longitude,  
        'maxradius': max_radius 
    }
    
    response = requests.get(url, params=params)
    
    if response.status_code == 200:
        data = response.json()
        new_earthquakes = []
        
        for feature in data['features']:
            if feature['properties']['type'] != 'earthquake':
                continue  

            # consider filtering for earthquakes with a magnitude of 1.0 or greater if this gets too spammy
            new_earthquakes.append(feature)
        
        return new_earthquakes
    else:
        print("Error fetching data:", response.status_code)
        return []

def post_to_threads(earthquakes):
    THREADS_USER_ID = os.getenv('THREADS_USER_ID')
    THREADS_ACCESS_TOKEN = os.getenv('THREADS_ACCESS_TOKEN')
    
    for earthquake in earthquakes:  
        magnitude = earthquake['properties']['mag']
        location = earthquake['properties']['place']
        coordinates = earthquake['geometry']['coordinates']
        lat, lon = coordinates[1], coordinates[0]  # USGS returns [lon, lat]
        
        google_maps_link = f"https://www.google.com/maps/place/{lat}+{lon}/@{lat},{lon},10z"
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

        post_message = f"{prefix} {magnitude} magnitude earthquake occurred near {location}."
        details_message = f" Details: {usgs_link}"

        if len(post_message) + len(details_message) <= 500:
            post_message += details_message

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
