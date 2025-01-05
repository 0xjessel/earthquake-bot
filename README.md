# @sfearthquakealerts

<img src="https://raw.githubusercontent.com/0xjessel/earthquake-bot/main/images/profile_pic.png" alt="Profile Pic" width="300px">

Follow my [Threads profile](https://www.threads.net/@sfearthquakealerts)!

# Overview

`fetch_and_post_earthquake.py` is run via a cron job on Dreamhost that is scheduled to run every 5 minutes.

`fetch_new_earthquakes()` gets the latest data from the USGS API. Using SFO Airport as the reference point, the script monitors earthquakes within a 300-mile radius and filters them based on distance and magnitude:

- Within 25 miles: Reports ALL earthquakes
- 25-50 miles: Reports earthquakes M2.0 and above
- 50-100 miles: Reports earthquakes M4.0 and above
- 100-250 miles: Reports earthquakes M5.0 and above
- 250-300 miles: Reports earthquakes M7.0 and above

`post_to_threads()` takes the earthquake data and calls the threads API to publish a post. I include the USGS link to the earthquake details plus a google maps link to the coordinates of the earthquake epicenter.

I also schedule `th_access_token.py` to be a cron job that's run every month to keep the access token valid.

# Instructions

1. **Clone the repository:**

   ```bash
   git clone https://github.com/0xjessel/earthquake-bot.git
   cd earthquake-bot
   ```

2. **Create a virtual environment:**

   ```bash
   python -m venv venv
   ```

3. **Activate the virtual environment:**

   - On Windows:
     ```bash
     venv\Scripts\activate
     ```
   - On macOS/Linux:
     ```bash
     source venv/bin/activate
     ```

4. **Install the required packages:**

   ```bash
   pip install -r requirements.txt
   ```

5. **Create a `.env.local` file:**

   - Copy the `.env.example` file to create your own environment configuration:
     ```bash
     cp .env.example .env.local
     ```

6. **Edit the `.env.local` file:**

   - Open `.env.local` in a text editor and fill in the required values

7. **Run the script**

```bash
python fetch_and_post_earthquake.py
```

8. To update the cron job on dreamhost server

```bash
scp fetch_and_post_earthquake.py meltedxice@iad1-shared-b8-41.dreamhost.com:~/cron_jobs/ba-earthquake-bot/
```

9. Update the .env.local file if it changed

# Random thoughts

What other bots can I make hmm..
