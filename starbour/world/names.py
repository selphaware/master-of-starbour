"""Original name generators for captains, ships, planets and presidents.

[ASSUMED] D-90 / D-146: syllable and word lists of our own; no franchise names anywhere
(D-03).  Every generator draws only from the RNG it is given (D-67).  M4 widens the pools to
60 first names and 72 surnames so a sector of eight captains rarely repeats a first name.
"""
from __future__ import annotations

import random

FIRST_NAMES = (
    "Harrow", "Ilse", "Teodor", "Maren", "Caspian", "Odile", "Bram", "Sunniva", "Lorcan", "Vesna",
    "Ansel", "Thea", "Rafe", "Imke", "Corwin", "Saskia", "Emrys", "Linnea", "Hollis", "Zora",
    "Idris", "Wren", "Osric", "Petra", "Kael", "Nerys", "Tobiah", "Freya", "Aldous", "Mirren",
    "Jorah", "Elin", "Dacre", "Yseult", "Ragnar", "Anouk", "Fenwick", "Tamsin", "Lucan", "Brisa",
    "Avel", "Cerys", "Dael", "Eirlys", "Faelan", "Garnet", "Jessamy", "Kerensa", "Lowen", "Merryn",
    "Nessa", "Oswin", "Perran", "Quenby", "Rowan", "Sorrel", "Ursel", "Vesper", "Xanthe", "Zennor",
)

SURNAMES = (
    "Vey", "Marrow", "Ashgrove", "Quill", "Stannard", "Holloway", "Pike", "Varga", "Lindqvist", "Mortlake",
    "Rook", "Caddel", "Brannock", "Sayer", "Thorne", "Oakes", "Fairweather", "Dunmore", "Kestle", "Ravel",
    "Harlow", "Penhallow", "Ostrander", "Blythe", "Carrick", "Wyndham", "Selkirk", "Treloar", "Voss", "Ingram",
    "Ambrey", "Bellamy", "Calloway", "Draycott", "Elsworth", "Fairlie", "Garrow", "Hanbury", "Inchcape", "Jessop",
    "Kirkbride", "Lanyon", "Merrow", "Nankervis", "Orrell", "Polwhele", "Quarrie", "Rosevear", "Sallow", "Tregenna",
    "Umfraville", "Vasey", "Wendle", "Yeoward", "Axhill", "Boscawen", "Crewe", "Denholm", "Eastlake", "Furneaux",
    "Gillow", "Hesketh", "Iredale", "Jago", "Kettering", "Lisle", "Maddox", "Nethercott", "Oddie", "Pellew",
    "Rudd", "Scorrier",
)

SHIP_ADJECTIVES = (
    "Grey", "Pale", "Quiet", "Iron", "Amber", "Silent", "Distant", "Northern", "Wandering", "Patient",
    "Lesser", "Brazen", "Hollow", "Scarlet", "Winter", "Bitter", "Gilded", "Sable", "Crooked", "Second",
)

SHIP_NOUNS = (
    "Lantern", "Heron", "Compass", "Ember", "Tern", "Anvil", "Harrier", "Kettle", "Mariner", "Plover",
    "Sextant", "Gannet", "Furrow", "Cinder", "Beacon", "Osprey", "Keel", "Tallow", "Pennant", "Curlew",
    "Halyard", "Bellwether", "Sparrow", "Tinder", "Wharf", "Ballast", "Marlin", "Fathom", "Gull", "Lodestar",
)

PLANET_STARTS = ("Or", "Vel", "Tam", "Kes", "Ar", "Bel", "Dun", "Eth", "Fal", "Gal", "Hal", "Isk", "Jor", "Kal", "Lum",
                 "Mor", "Nar", "Osk", "Pel", "Quor", "Ren", "Sol", "Tor", "Ul", "Var", "Wen", "Yar", "Zel")
PLANET_MIDDLES = ("a", "e", "i", "o", "u", "ae", "ia", "io", "ou", "y")
PLANET_ENDS = ("th", "ris", "dan", "mir", "non", "vek", "lis", "sa", "rum", "nd", "ph", "x", "ndra", "sk", "tha", "ven")


def captain_name(rng: random.Random) -> str:
    """First name and surname, e.g. Harrow Vey - CP-01."""
    return f"{rng.choice(FIRST_NAMES)} {rng.choice(SURNAMES)}"


def ship_name(rng: random.Random) -> str:
    """One noun or adjective + noun, e.g. Lantern, Grey Heron - SH-08."""
    if rng.random() < 0.5:
        return rng.choice(SHIP_NOUNS)
    return f"{rng.choice(SHIP_ADJECTIVES)} {rng.choice(SHIP_NOUNS)}"


def planet_name(rng: random.Random) -> str:
    """Start + middle + end syllables, e.g. Oreth - PL-01."""
    return rng.choice(PLANET_STARTS) + rng.choice(PLANET_MIDDLES) + rng.choice(PLANET_ENDS)


def president_name(rng: random.Random) -> str:
    """The head of a planet - PL-05."""
    return captain_name(rng)


def unique(generator, rng: random.Random, used: set[str]) -> str:
    """Draw until a name not in ``used`` appears (bounded), then record it.

    The retry loop is deterministic for a given RNG state and ``used`` contents, so it
    keeps D-67.  After many collisions a numeric suffix is added instead of looping on.
    """
    for _ in range(50):
        name = generator(rng)
        if name not in used:
            used.add(name)
            return name
    base = generator(rng)
    n = 2
    while f"{base} {n}" in used:
        n += 1
    name = f"{base} {n}"
    used.add(name)
    return name
