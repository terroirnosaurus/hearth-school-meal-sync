# Grant Elementary meals → Hearth calendar

Turns the Petaluma City Schools breakfast and lunch menus
([menus.healthepro.com](https://menus.healthepro.com/organizations/472/sites/4063/menus/129804))
into a subscribable calendar feed (`.ics`) for the Hearth Display, Google Calendar, Apple Calendar, or Outlook.

Each school day gets an all-day breakfast event and an all-day lunch event, for example:

```
Tue Sep 22  all day      🥞 Breakfast: Pancakes
Tue Sep 22  all day      🥪 Lunch: Grilled Cheese Sandwich or BBQ Beef Rib Sandwich
                              Vegetables: Salad Bar
                              Fruit: Fresh Oranges, Fresh Apples
Mon Oct 12  all day      🏠 No School Meals: Professional Development Day
```

## How it works

1. `school_menu_sync.py` reads the public JSON API behind the menu site (no HTML scraping) and writes
   `docs/grant-elementary-meals.ics`. It finds menus by name ("Elementary Breakfast", "Elementary Lunch") and
   takes the newest match, so it keeps working when next school year's menu is published.
2. A GitHub Actions workflow runs it every morning and commits the file only when the menu changed.
3. GitHub Pages serves `docs/`, giving the feed a stable URL that the calendar subscribes to.

## Run it locally

Needs Python 3.9+ and nothing else.

```bash
python3 school_menu_sync.py --preview   # print what the calendar will contain
python3 school_menu_sync.py             # write docs/grant-elementary-meals.ics
python3 -m unittest discover tests      # offline tests
```

## Publish the feed (one-time)

1. Create a **public** GitHub repo and push this project to `main`.
2. Repo **Settings → Pages**: Source "Deploy from a branch", branch `main`, folder `/docs`.
3. Repo **Settings → Actions → General → Workflow permissions**: "Read and write permissions".
4. The feed URL will be `https://<your-username>.github.io/<repo-name>/grant-elementary-meals.ics`.

## Add it to Hearth

Either option works:

- **Directly in Hearth**: add a calendar by URL and paste the feed URL.
- **Through Google Calendar**: on a computer, Google Calendar → Other calendars **+** → **From URL** → paste the
  feed URL. Then enable that calendar in the Hearth app. Google refreshes URL calendars on its own schedule
  (typically within a day), which is fine for a monthly menu.

## Customize (`config.json`)

| Setting | What it does |
| --- | --- |
| `meals[].start` / `end` | Optional event times (e.g. `"start": "11:45", "end": "12:15"`). Leave them out for all-day events (the default). |
| `meals[].emoji` | Fallback emoji when no `food_emoji` rule matches the entree. |
| `title_categories` | Categories shown in the event title (the entrees). |
| `description_categories` | Categories listed in the event details. Milk and condiments are left out by default. |
| `include_days_off` | Adds an all-day "No School Meals" event on holidays and PD days. |
| `name_cleanup` | Regex find/replace rules that shorten item names (e.g. drops "FEED Special - "). |
| `food_emoji` | Regex → emoji rules; the first match on the first entree wins. |

For a different school in the same district, change `site_id` (from the menu page URL) and `menu_name_contains`.
