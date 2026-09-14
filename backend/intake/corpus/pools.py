"""Word pools the seeded generator draws from.

Data only -- no randomness here, so the pools can be extended without changing
what any existing seed produces for entities generated before the extension point.
"""

from __future__ import annotations

ORG_TOKENS = [
    "Meridian", "Ashgrove", "Northwind", "Calder", "Vantage", "Brightwater",
    "Pinehurst", "Sterling", "Kestrel", "Halcyon", "Ironwood", "Copperfield",
    "Lakeshore", "Summit Ridge", "Fairmont", "Beacon Hill", "Cobalt", "Thornbury",
    "Windermere", "Granite Bay", "Silverton", "Ridgeline", "Blackstone Hollow",
    "Harborview", "Lindenwood", "Westbrook", "Cardinal", "Juniper", "Marlowe",
    "Oakhaven", "Presidio", "Quarry Lane", "Redbridge", "Stonegate", "Talmadge",
]

ORG_NOUNS = [
    "Capital Partners", "Logistics", "Holdings", "Medical Group", "Property Group",
    "Systems", "Foods", "Industries", "Diagnostics", "Construction", "Analytics",
    "Freight", "Dental Associates", "Manufacturing", "Ventures", "Distribution",
    "Robotics", "Fabrication", "Hospitality Group", "Orthopedics", "Textiles",
    "Energy", "Automotive Group", "Biosciences", "Aggregates", "Staffing",
]

ORG_SUFFIXES = ["LLC", "Inc.", "LLP", "Corp.", "Ltd.", "L.L.C.", "PLLC", "Co."]

FIRST_NAMES = [
    "Dana", "Marcus", "Priya", "Eleanor", "Terrence", "Joaquin", "Rosalind",
    "Desmond", "Anika", "Curtis", "Imani", "Roland", "Beatriz", "Nikolai",
    "Saoirse", "Emmett", "Yuki", "Gerald", "Constance", "Malik", "Delphine",
    "Aurelio", "Harriet", "Tobias", "Ingrid", "Ramona", "Victor", "Lucia",
    "Barnaby", "Felicity", "Osman", "Greta", "Xavier", "Noor", "Clement",
]

LAST_NAMES = [
    "Okafor", "Whitfield", "Delacroix", "Nakamura", "Bellweather", "Vasquez",
    "Ashford", "Lindqvist", "Moreau", "Castellanos", "Redmond", "Achebe",
    "Thornton", "Petrov", "Kavanagh", "Sandoval", "Weatherby", "Ibarra",
    "Fairchild", "Mbeki", "Larkin", "Rosetti", "Holloway", "Duvall", "Amador",
    "Stapleton", "Ferreira", "Winslow", "Kowalski", "Bramble", "Ortega",
]

JURISDICTIONS = [
    "Cook County, Illinois", "Travis County, Texas", "King County, Washington",
    "Middlesex County, Massachusetts", "Maricopa County, Arizona",
    "Fulton County, Georgia", "Allegheny County, Pennsylvania",
    "Hennepin County, Minnesota", "Multnomah County, Oregon",
    "Wake County, North Carolina", "Bexar County, Texas", "Denver County, Colorado",
]

STREETS = [
    "Larkspur Ave", "Foundry St", "Kestrel Lane", "Bellamy Rd", "Oak Hollow Dr",
    "Sable Court", "Gable Way", "Dunmore St", "Wexford Ave", "Pelham Rd",
]

CITIES = [
    "Riverton", "Fairhaven", "Northgate", "Westfield", "Cedar Falls",
    "Hollis Park", "Brookvale", "Ainsworth",
]

FREE_MAIL_DOMAINS = ["gmail.com", "outlook.com", "protonmail.com", "yahoo.com"]

# Matter caption shapes, by practice area.
CAPTION_SHAPES = {
    "commercial_litigation": "{client} v. {adverse}",
    "employment": "{client} -- Employment Matter ({adverse})",
    "real_estate": "{client} -- {adverse} Property Transaction",
    "intellectual_property": "{client} v. {adverse} (Trademark)",
    "family": "In re Marriage of {client}",
    "personal_injury": "{client} v. {adverse} (Personal Injury)",
}

ATTORNEY_SEEDS = [
    ("Harriet Vance", ["commercial_litigation", "intellectual_property"], 12),
    ("Desmond Ellery", ["commercial_litigation"], 10),
    ("Priyanka Raghavan", ["employment", "commercial_litigation"], 11),
    ("Nathaniel Brock", ["employment"], 9),
    ("Sylvia Okonkwo", ["real_estate"], 10),
    ("Gerald Lindqvist", ["real_estate", "commercial_litigation"], 8),
    ("Mireille Santos", ["intellectual_property"], 9),
    ("Owen Trebuchet", ["family"], 10),
    ("Camille Aldridge", ["family", "employment"], 8),
    ("Rafael Domingo", ["personal_injury"], 12),
    ("Beatrice Holloway", ["personal_injury", "commercial_litigation"], 10),
]
