import requests
from datetime import datetime, timedelta, timezone

def fetch_new_earthquakes():
    url = "https://earthquake.usgs.gov/fdsnws/event/1/query"
    
    params = {
        'format': 'geojson',
        'starttime': (datetime.now(timezone.utc) - timedelta(minutes=500)).isoformat(),
        'endtime': datetime.now(timezone.utc).isoformat(),
        'latitude': 37.7749,  # Bay Area latitude
        'longitude': -122.4194,  # Bay Area longitude
        'maxradius': 1.45 
    }
    
    response = requests.get(url, params=params)
    
    if response.status_code == 200:
        data = response.json()
        new_earthquakes = []
        
        for feature in data['features']:
            if feature['properties']['type'] != 'earthquake':
                continue  

            if feature['properties']['mag'] >= 1.0:
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
        
        google_maps_link = f"https://www.google.com/maps/place/{lat}+{lon}/@{lat},{lon},10z"
        
        usgs_link = earthquake['properties']['url']
        
        post_message = (
            f"A {magnitude} magnitude earthquake occurred {location}. "
            f"Details: {usgs_link}. Link attachment: {google_maps_link}"
        )
        
        print(post_message)

if __name__ == "__main__":
    new_earthquakes = fetch_new_earthquakes()
    if new_earthquakes:
        post_to_thread(new_earthquakes)
    else:
        print("No new earthquakes found")
