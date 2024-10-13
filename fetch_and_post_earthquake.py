import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import os

load_dotenv(dotenv_path='.env.local')

usgs_api_url = os.getenv('USGS_API_URL')
latitude = os.getenv('LATITUDE')
longitude = os.getenv('LONGITUDE')
max_radius = os.getenv('MAX_RADIUS')
time_window = os.getenv('TIME_WINDOW')

def fetch_new_earthquakes():
    url = usgs_api_url
    print(url)
    
    params = {
        'format': 'geojson',
        'starttime': (datetime.now(timezone.utc) - timedelta(minutes=time_window)).isoformat(),
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

def post_to_thread(earthquakes):
    for earthquake in earthquakes:
        magnitude = earthquake['properties']['mag']
        location = earthquake['properties']['place']
        coordinates = earthquake['geometry']['coordinates']
        lat, lon = coordinates[1], coordinates[0]  # USGS returns [lon, lat]
        
        # Determine the prefix based on magnitude
        if magnitude < 4.0:
            prefix = "zzz..."
        elif 4.0 <= magnitude < 5.0:
            prefix = "Whoa!"
        else:
            prefix = "ALERT!"
        
        google_maps_link = f"https://www.google.com/maps/place/{lat}+{lon}/@{lat},{lon},10z"
        
        usgs_link = earthquake['properties']['url']
        
        post_message = (
            f"{prefix} A {magnitude} magnitude earthquake occurred near {location}. "
            f"Details: {usgs_link}. Link attachment: {google_maps_link}"
        )
        
        print(post_message)

if __name__ == "__main__":
    new_earthquakes = fetch_new_earthquakes()
    if new_earthquakes:
        post_to_thread(new_earthquakes)
    else:
        print("No new earthquakes found")
