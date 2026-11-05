# Long Weekends 2027

A long-weekend planner for trips from London, January to August 2027. It covers 160 destinations across Europe, Turkey, the Caucasus and North Africa. Every trip is 3–5 nights and always includes Friday and Saturday night.

Prices refresh **every morning by themselves**: a GitHub Action fetches live fares and commits them, and GitHub Pages serves the site.

## How it works

| Part | What it does |
|---|---|
| `index.html` | The planner: month tabs, filters, cost calculator and booking links |
| `data/destinations.json` | The 160 destinations, with estimates used as a fallback |
| `data/prices.json` | Live fares and hotel rates, rewritten daily by the bot. Don't edit it by hand |
| `scripts/fetch_prices.py` | Fetches the prices (Python standard library only, no installs) |
| `.github/workflows/refresh-prices.yml` | Runs the script every day at 05:15 UTC |
| `data/hotel_watchlist.txt` | Which destinations get live hotel prices |

**Flights** come from two free sources. The script keeps only trips that match the long-weekend rules: 3–5 nights, leaving Wed/Thu/Fri, including Fri and Sat night.
- **Ryanair's fare finder** (no key needed): live Ryanair prices from Stansted, Luton, Gatwick and Southend for every long-weekend pattern up to the end of August. That's about 840 polite requests a day, each covering all Ryanair destinations at once. This isn't an official public service; if Ryanair ever blocks it, the planner falls back to the other sources.
- **[Travelpayouts](https://www.travelpayouts.com) (Aviasales) Data API**: the cheapest London fares on every other airline (easyJet, Wizz Air, BA, Jet2, Vueling and others) found in searches over the last day or two. It's thin for dates far ahead and fills in as they get closer.

**Hotels** (optional) come from [SerpApi](https://serpapi.com)'s Google Hotels results: 3★+ hotels rated 4.0+, using the median of the 10 cheapest. The free plan has a small monthly allowance, so only destinations in `hotel_watchlist.txt` are refreshed, 3 searches a day.

Where there's no live price, the card shows an **estimate** and says so.

## Setup (about 15 minutes, one time)

1. **Get a Travelpayouts token (free).** Sign up at travelpayouts.com, then copy your API token from the dashboard (it's under the API / Data API section of your profile).
2. **Optional: get a SerpApi key** at serpapi.com (free plan) for live hotel prices.
3. **Create a GitHub repository.** On github.com click **New repository**, name it `long-weekends-2027` and make it **Public** (free GitHub Pages needs a public repo). Don't add a README.
4. **Upload this folder.** In Terminal:
   ```sh
   cd ~/long-weekends-2027
   git init -b main
   git add .
   git commit -m "Long Weekends 2027 planner"
   git remote add origin https://github.com/YOUR-USERNAME/long-weekends-2027.git
   git push -u origin main
   ```
5. **Add the secrets.** In the repo, go to **Settings → Secrets and variables → Actions → New repository secret**:
   - `TRAVELPAYOUTS_TOKEN`: your token
   - `SERPAPI_KEY`: your SerpApi key (optional)
6. **Turn on the website.** Go to **Settings → Pages**, choose **Deploy from a branch**, pick `main` and `/ (root)`, then click Save.
7. **Fill the prices the first time.** Go to **Actions → Refresh prices → Run workflow**. Each run takes about 40 minutes, because it searches slowly to stay polite to Ryanair and Travelpayouts.

Your planner will then be at `https://YOUR-USERNAME.github.io/long-weekends-2027/`. It updates every morning, and anyone with the link can open it.

## Preview on your computer

Browsers block the data files when you open `index.html` directly, so run a tiny local server:

```sh
cd ~/long-weekends-2027 && python3 -m http.server 8000
```

Then open http://localhost:8000.

## Changing things

- **Live hotel prices for more places:** add destination ids (from `data/destinations.json`) to `data/hotel_watchlist.txt`.
- **Refresh time:** edit the `cron` line in `.github/workflows/refresh-prices.yml`.
- **Run tests:** `python3 -m unittest discover -s tests`. The tests also run before every daily refresh.

## Limitations

- **"Live" means refreshed daily, not to the second.** Travelpayouts reports fares found in recent searches, so a quiet route may have no live fare for some dates. Those show as estimates. Always use the card buttons for the exact price before booking.
- **Ryanair is complete; other airlines are partial.** easyJet, Wizz Air and others protect their own price feeds, so they only appear when Travelpayouts has seen a recent search for those dates.
- **Hotel prices are limited** by SerpApi's free allowance. For full live hotel coverage you'd need a paid plan (about $75 a month).
- **GitHub pauses scheduled jobs** in repos with no activity for 60 days. The daily price commits count as activity, so this only happens if the refresh keeps failing (for example, an expired token). GitHub emails you when a run fails.
