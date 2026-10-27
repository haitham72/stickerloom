"""Several subjects in one request: "create three sticker packs of fruits" is a NEW request that names a count and a category, not one subject.

`parse_multi` recognises it (a count of 2..6, pack / set words, a category); `pick` chooses that many different, concrete subjects of the category: the local model when there is one (it is asked for varied,
not the three most obvious), else a seeded random draw from the built-in lists below, avoiding what this chat already made. Pure: no model client here (`pick` takes the model as a function), no files."""
from __future__ import annotations

import random
import re

MAX_SUBJECTS = 6
NUMBERS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
_COUNT = r"(?P<n>\d+|two|three|four|five|six)"
_SETS = r"(?:sticker\s+)?(?:packs?|sets?|batches|sheets|collections?)"
_VERBS = r"(?:please\s+)?(?:(?:can|could|would|will) you\s+)?(?:make|create|generate|give|draw|design|build|do|i want|i need|i'd like)\s+(?:me\s+)?"
MULTI = [
    re.compile(rf"^{_VERBS}{_COUNT}\s+(?:different\s+|separate\s+|new\s+)?{_SETS}\s+(?:of|with|about|for|on|from)\s+(?P<cat>.+?)[\s.!?]*$"),
    re.compile(rf"^{_VERBS}{_COUNT}\s+(?:different\s+|separate\s+|new\s+)?(?P<cat>[a-z][a-z ]{{1,30}}?)\s+{_SETS}[\s.!?]*$"),
]

CATEGORIES: dict[str, list[str]] = {
    "fruit": ["strawberry", "cherries", "banana", "apple", "orange", "watermelon", "pineapple", "grapes", "mango", "peach", "lemon", "kiwi", "pear", "blueberries", "coconut", "dates", "pomegranate", "fig"],
    "vegetable": ["carrot", "tomato", "broccoli", "corn", "eggplant", "pepper", "potato", "onion", "pumpkin", "cucumber", "mushroom", "garlic"],
    "animal": ["cat", "dog", "panda", "fox", "owl", "rabbit", "penguin", "lion", "elephant", "monkey", "koala", "giraffe", "tiger", "hedgehog", "camel", "falcon"],
    "pet": ["cat", "dog", "rabbit", "hamster", "parrot", "goldfish", "turtle", "guinea pig"],
    "bird": ["falcon", "owl", "parrot", "penguin", "flamingo", "duck", "eagle", "peacock", "hummingbird", "swan"],
    "sea creature": ["octopus", "dolphin", "whale", "crab", "jellyfish", "seahorse", "shark", "turtle", "starfish"],
    "insect": ["butterfly", "bee", "ladybug", "ant", "dragonfly", "snail", "caterpillar"],
    "food": ["pizza", "burger", "sushi", "taco", "donut", "fries", "hot dog", "ice cream", "noodles", "pancakes", "popcorn", "cheese", "falafel", "shawarma"],
    "dessert": ["cupcake", "donut", "ice cream", "chocolate", "cookie", "cake slice", "macaron", "waffle", "baklava", "pudding"],
    "drink": ["coffee", "tea", "orange juice", "milkshake", "lemonade", "bubble tea", "soda", "espresso"],
    "flower": ["rose", "sunflower", "tulip", "daisy", "lotus", "lavender", "cherry blossom", "orchid"],
    "vehicle": ["car", "bus", "bicycle", "airplane", "rocket", "train", "boat", "helicopter", "scooter", "tractor"],
    "sport": ["football", "basketball", "tennis", "swimming", "cycling", "boxing", "golf", "volleyball", "yoga", "running"],
    "weather": ["sun", "rain cloud", "snowflake", "rainbow", "lightning", "wind", "moon", "storm cloud"],
    "emotion": ["happy face", "sad face", "angry face", "surprised face", "sleepy face", "in love face"],
    "job": ["doctor", "chef", "pilot", "teacher", "firefighter", "astronaut", "farmer", "artist", "police officer"],
    "space": ["rocket", "astronaut", "planet", "alien", "comet", "satellite", "telescope", "moon"],
    "instrument": ["guitar", "piano", "drum", "violin", "trumpet", "saxophone", "harp"],
    "holiday": ["eid", "ramadan", "birthday", "new year", "national day", "christmas", "wedding", "graduation"],
    "desert": ["camel", "cactus", "sand dune", "scorpion", "oasis", "tumbleweed", "snake", "sun"],
    "toy": ["teddy bear", "robot", "kite", "balloon", "yo-yo", "building blocks", "rubber duck", "puzzle"],
    "school": ["backpack", "pencil", "book", "notebook", "ruler", "globe", "calculator", "scissors"],
    "dinosaur": ["t-rex", "triceratops", "stegosaurus", "brachiosaurus", "pterodactyl", "velociraptor"],
    "tool": ["hammer", "wrench", "screwdriver", "saw", "drill", "paint roller"],
    "clothes": ["t-shirt", "sneakers", "hat", "sunglasses", "scarf", "dress", "backpack"],
}
ALIASES = {"fruits": "fruit", "vegetables": "vegetable", "veggies": "vegetable", "veggie": "vegetable", "animals": "animal", "pets": "pet", "birds": "bird", "sea animals": "sea creature",
           "sea creatures": "sea creature", "ocean animals": "sea creature", "insects": "insect", "bugs": "insect", "foods": "food", "meals": "food", "desserts": "dessert", "sweets": "dessert",
           "drinks": "drink", "beverages": "drink", "flowers": "flower", "vehicles": "vehicle", "cars": "vehicle", "sports": "sport", "emotions": "emotion", "feelings": "emotion",
           "jobs": "job", "professions": "job", "instruments": "instrument", "holidays": "holiday", "occasions": "holiday", "deserts": "desert", "toys": "toy", "dinosaurs": "dinosaur",
           "tools": "tool", "clothing": "clothes", "space things": "space"}


def parse_multi(text: str):
    """(count, category) for "create three sticker packs of fruits" / "make 3 fruit packs"; None for anything else. A count above the limit is clamped to it."""
    t = " ".join(text.lower().split())
    for rx in MULTI:
        m = rx.match(t)
        if m:
            n = int(m["n"]) if m["n"].isdigit() else NUMBERS[m["n"]]
            cat = re.sub(r"^(?:some|the|different|various|cute|nice|funny)\s+", "", m["cat"].strip(" ."))
            if n < 2 or not cat or len(cat.split()) > 4:
                return None
            return min(n, MAX_SUBJECTS), cat
    return None


def category_of(cat: str) -> str | None:
    """The built-in list a category word belongs to ("fruits" -> "fruit"), or None when there is none."""
    c = " ".join(cat.lower().split())
    if c in CATEGORIES:
        return c
    if c in ALIASES:
        return ALIASES[c]
    if c.endswith("ies") and c[:-3] + "y" in CATEGORIES:
        return c[:-3] + "y"
    if c.endswith("s") and c[:-1] in CATEGORIES:
        return c[:-1]
    for w in c.split():
        if w in CATEGORIES:
            return w
        if w in ALIASES:
            return ALIASES[w]
    return None


def _stem(w: str) -> str:
    w = w.lower().strip()
    if w.endswith("ies"):
        return w[:-3] + "y"
    return re.sub(r"(?:es|s)$", "", w) if len(w) > 3 else w


def _same(a: str, b: str) -> bool:
    return bool({_stem(x) for x in re.findall(r"[a-z]+", a.lower())} & {_stem(x) for x in re.findall(r"[a-z]+", b.lower())})


def clean(names, n: int, avoid=()) -> list[str]:
    """What a model answered, made safe: distinct, 1 to 3 words, no 'sticker', not one of `avoid`, at most n."""
    out: list[str] = []
    for x in names or []:
        s = " ".join(re.sub(r"[^A-Za-z0-9 '\-]", " ", str(x)).split())[:40].strip().lower()
        if not s or len(s.split()) > 3 or re.search(r"\bsticker", s):
            continue
        if any(_same(s, o) for o in list(out) + list(avoid)):
            continue
        out.append(s)
    return out[:n]


def pick(cat: str, n: int, avoid=(), ask=None, seed=None) -> tuple[list[str], str]:
    """(subjects, by). `ask(category, n, avoid) -> list | None` is the model (asked first, its answer is cleaned); what it does not fill comes from a seeded random draw of the built-in list of
    the category. ([], "none") when nothing can be chosen (an unknown category and no model): the caller asks the person for the subjects."""
    avoid = list(avoid)
    got = clean(ask(cat, n, avoid), n, avoid) if ask else []
    if len(got) >= n:
        return got, "model"
    key = category_of(cat)
    pool = [x for x in CATEGORIES.get(key, []) if not any(_same(x, o) for o in avoid + got)]
    random.Random(seed).shuffle(pool)
    out = got + pool[: n - len(got)]
    if not out:
        return [], "none"
    return out, ("model+table" if got else "table")
