### IATA airport list, app/data/iata_airports.json

Built by `scripts/data/refresh_iata_airports.py` from the airport snapshot of
meshloom-community (`api/airports/ourairports_snapshot.csv` and `metro_codes.csv`).

- **OurAirports** (https://ourairports.com/data/): public domain.
- **OpenTravelData** (https://github.com/opentraveldata/opentraveldata), the
  aerodrome codes OurAirports does not carry: licensed under the Creative Commons
  Attribution 4.0 International licence (CC BY 4.0,
  https://creativecommons.org/licenses/by/4.0/). Modified: merged with OurAirports
  and recomputed (positions and types taken from OurAirports where they match,
  metropolitan area codes placed at the mean of their airports).
- Metropolitan area codes: from Wikivoyage's list of IATA metropolitan area codes,
  as versioned in meshloom-community. Only the codes (facts) are used; Wikivoyage's
  text is licensed CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0/).
