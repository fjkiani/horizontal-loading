"""Validated seed roster for on-demand category generation.

The console previously served one frozen trap per category, and /api/generate
was wired only to the loc.gov newspaper walk, so "generate" could not produce a
category trap at all. This is the seed grid that the expansion sweep actually
measured: 81 seeds across 16 categories, of which 53 produced a gate-valid
trap. The failures are kept in the roster rather than pruned, because a seed
that fails the gate fails it loudly with a stated reason -- an argmax sitting
at an endpoint, a population too small to clear the guessability ceiling, a
witness that turns out to be run by the primary operator -- and that refusal is
the system working, not an error to hide.

Seeds are dicts of generator kwargs. They are validated against the live
generator signature before use, so a seed that no longer binds fails at the
request rather than silently falling back to the default trap.
"""
import inspect
import itertools
import threading

import category_traps as ct
import gen_v2  # noqa: F401  installs the v2 overrides
import gen_v3  # noqa: F401  installs the field redesign

GRID = {
    "science and technology": [
        {},
        {"days": ("2023-02-14", "2023-05-16", "2023-09-12", "2023-11-14", "2024-09-10", "2024-10-08"),
         "cats": ("cs.CR", "math.PR", "cond-mat.mes-hall", "astro-ph.GA", "q-bio.PE", "physics.flu-dyn")},
        {"days": ("2022-03-08", "2022-06-14", "2022-10-11", "2023-01-10", "2023-04-11", "2023-07-11"),
         "cats": ("cs.LG", "math.CO", "cond-mat.supr-con", "astro-ph.HE", "q-bio.NC", "physics.optics")},
        {"days": ("2021-05-11", "2021-09-14", "2022-01-11", "2022-04-12", "2022-08-09", "2022-11-08"),
         "cats": ("cs.DS", "math.NT", "cond-mat.soft", "astro-ph.SR", "q-bio.QM", "physics.plasm-ph")},
    ],
    "art": [
        {}, {"artist": "Vincent van Gogh", "dept": 11}, {"artist": "Claude Monet", "dept": 11},
        {"artist": "Katsushika Hokusai", "dept": 6}, {"artist": "Albrecht Durer", "dept": 9},
        {"artist": "Paul Cezanne", "dept": 11},
    ],
    "business": [
        {}, {"loc": "US-WA", "concept": "ResearchAndDevelopmentExpense", "year": 2018},
        {"loc": "US-CA", "concept": "ResearchAndDevelopmentExpense", "year": 2016},
        {"loc": "US-MA", "concept": "ResearchAndDevelopmentExpense", "year": 2017},
        {"loc": "US-NY", "concept": "ResearchAndDevelopmentExpense", "year": 2019},
        {"loc": "US-IL", "concept": "ResearchAndDevelopmentExpense", "year": 2015},
    ],
    "celebrities/public figures": [
        {}, {"category_key": "Chemistry", "y0": 1901, "y1": 1975},
        {"category_key": "Physiology or Medicine", "y0": 1901, "y1": 1970},
        {"category_key": "Literature", "y0": 1901, "y1": 1980},
        {"category_key": "Peace", "y0": 1901, "y1": 1975},
    ],
    "education": [
        {}, {"country": "Norway"}, {"country": "Portugal"}, {"country": "Finland"},
        {"country": "Israel"}, {"country": "Chile"}, {"country": "Hungary"}, {"country": "Denmark"},
    ],
    "geography": [
        {}, {"country_iso": "CH", "country_name": "Switzerland"},
        {"country_iso": "PE", "country_name": "Peru"}, {"country_iso": "NP", "country_name": "Nepal"},
        {"country_iso": "BO", "country_name": "Bolivia"}, {"country_iso": "EC", "country_name": "Ecuador"},
        {"country_iso": "KE", "country_name": "Kenya"},
    ],
    "health and medicine": [
        {}, {"condition": "multiple sclerosis", "phase": "PHASE3"},
        {"condition": "idiopathic pulmonary fibrosis", "phase": "PHASE3"},
        {"condition": "sickle cell disease", "phase": "PHASE3"},
        {"condition": "Duchenne muscular dystrophy", "phase": "PHASE3"},
        {"condition": "cystic fibrosis", "phase": "PHASE3"},
    ],
    "history": [
        {}, {"category_key": "Chemistry", "y0": 1901, "y1": 2000, "min_laureates": 3},
        {"category_key": "Physiology or Medicine", "y0": 1901, "y1": 2000, "min_laureates": 3},
        {"category_key": "Physics", "y0": 1930, "y1": 1990, "min_laureates": 3},
    ],
    "legal": [
        {}, {"vols": (520, 524, 530, 533)}, {"vols": (540, 545, 550, 555)},
        {"vols": (460, 465, 470, 475)}, {"vols": (480, 485, 490, 495)},
    ],
    "politics": [
        {}, {"years": (1997, 1999, 2005, 2009)}, {"years": (2011, 2017, 2019, 2021)},
        {"years": (1993, 1995, 2007, 2012)},
    ],
    "sports": [
        {},
        {"pairs": ((112, "Chicago Cubs", 2016), (120, "Washington Nationals", 2019),
                   (137, "San Francisco Giants", 2010), (114, "Cleveland Indians", 1995),
                   (115, "Colorado Rockies", 2007))},
        {"pairs": ((121, "New York Mets", 1986), (143, "Philadelphia Phillies", 1980),
                   (110, "Baltimore Orioles", 1983), (116, "Detroit Tigers", 1984),
                   (138, "St. Louis Cardinals", 2011))},
        {"pairs": ((136, "Seattle Mariners", 2001), (139, "Tampa Bay Rays", 2008),
                   (133, "Oakland Athletics", 1989), (135, "San Diego Padres", 1998),
                   (141, "Toronto Blue Jays", 1993))},
    ],
    "travel": [
        {}, {"airline_iata": "LH", "hub_iata": "FRA"}, {"airline_iata": "SK", "hub_iata": "CPH"},
        {"airline_iata": "OS", "hub_iata": "VIE"}, {"airline_iata": "LO", "hub_iata": "WAW"},
        {"airline_iata": "TP", "hub_iata": "LIS"},
    ],
    "tv shows and movies": [
        {},
        {"seeds": ((1996, "Crime"), (2001, "Mystery"), (2005, "Sci-Fi"), (1999, "Fantasy"),
                   (2010, "Musical"), (1994, "Crime"))},
        {"seeds": ((1988, "Western"), (1992, "War"), (2007, "Biography"), (2013, "Adventure"),
                   (1985, "Horror"), (2016, "Animation"))},
    ],
    "video games": [
        {},
        {"appids": (400, 292030, 379720, 588650, 264710, 646570, 204360, 49520, 233450,
                    275850, 8930, 294100, 219740, 632470)},
        {"appids": (620, 240, 550, 730, 570, 440, 10, 70, 220, 320, 360, 380, 420, 500)},
    ],
    "finance": [{}, {"year": 2010}, {"year": 2014}, {"year": 2021}, {"year": 2023}],
    "shopping": [
        {}, {"category_tag": "en:chocolates", "country": "france", "nutrient": "fat_100g", "max_pages": 6},
        {"category_tag": "en:breakfast-cereals", "country": "united-kingdom", "nutrient": "fat_100g", "max_pages": 6},
        {"category_tag": "en:biscuits", "country": "belgium", "nutrient": "fat_100g", "max_pages": 6},
        {"category_tag": "en:cheeses", "country": "switzerland", "nutrient": "fat_100g", "max_pages": 6},
    ],
}

# Repeated calls with no explicit seed must produce a NOVEL trap, otherwise
# "generate" just re-serves the frozen answer that prompted this work.
_CURSOR = {}
_LOCK = threading.Lock()


def seeds_for(category):
    return list(GRID.get(category) or [{}])


def next_seed(category):
    """Rotate through the roster so successive calls differ."""
    with _LOCK:
        i = _CURSOR.get(category, 0)
        _CURSOR[category] = i + 1
    roster = seeds_for(category)
    return roster[i % len(roster)], i % len(roster)


def validate_kwargs(category, kwargs):
    """Reject a seed the generator cannot bind, instead of ignoring it."""
    fn = ct.GENERATORS.get(category)
    if fn is None:
        raise KeyError(category)
    sig = inspect.signature(fn)
    unknown = sorted(set(kwargs) - set(sig.parameters))
    if unknown:
        raise TypeError("unknown seed parameters for %r: %s; accepted: %s"
                        % (category, unknown, sorted(sig.parameters)))
    sig.bind_partial(**kwargs)
    return dict(kwargs)


def roster_summary():
    return {c: len(seeds_for(c)) for c in ct.GENERATORS}
