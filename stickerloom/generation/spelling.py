"""Obvious misspellings of the words a request keeps ("camel in lamborgini" -> "camel in Lamborghini"), so the plan, its cells and the card say the thing that was meant.

Only words of 6+ letters that are a near miss (difflib ratio >= 0.85) of a word in VOCAB are changed, and a word that is already in VOCAB never is: no dictionary, no model,
nothing guessed beyond a typo. VOCAB is the proper nouns people put in sticker requests (vehicles, brands, places, landmarks) plus the longer animals and things the rules
see often; proper nouns keep their capital. Extend it when a request shows a typo it misses."""
from __future__ import annotations

import difflib
import re

PROPER = ("Lamborghini", "Ferrari", "Bugatti", "Maserati", "Mercedes", "Bentley", "Rolls-Royce", "Chevrolet", "Mustang", "Cadillac", "Volkswagen", "Toyota", "Nissan",
          "Hyundai", "Mitsubishi", "Harley-Davidson", "Kawasaki", "Yamaha", "Ducati", "Tesla", "Lexus", "Jaguar", "Peugeot", "Renault", "Suzuki", "Honda", "McLaren", "Aston Martin",
          "Cairo", "London", "Paris", "Berlin", "Madrid", "Rome", "Tokyo", "Seoul", "Istanbul", "Sydney", "New York", "Eiffel Tower", "Pyramids", "Sahara",
          "Ramadan", "Eid", "Diwali", "Christmas", "Halloween", "Valentine")
COMMON = ("camel", "falcon", "giraffe", "elephant", "penguin", "dolphin", "octopus", "kangaroo", "squirrel", "hamster", "rabbit", "turtle", "tortoise", "flamingo", "peacock",
          "cheetah", "leopard", "panther", "gorilla", "chimpanzee", "crocodile", "alligator", "dinosaur", "unicorn", "dragon", "butterfly", "ladybug", "hedgehog", "raccoon",
          "astronaut", "superhero", "princess", "pirate", "wizard", "ninja", "robot", "zombie", "vampire", "mermaid", "skeleton", "scientist", "firefighter", "footballer",
          "motorcycle", "helicopter", "submarine", "spaceship", "rocket", "bicycle", "skateboard", "convertible", "limousine", "tractor", "airplane", "sailboat", "yacht",
          "banana", "avocado", "pineapple", "watermelon", "strawberry", "chocolate", "cupcake", "doughnut", "croissant", "spaghetti", "sandwich", "hamburger", "popcorn",
          "umbrella", "sunglasses", "headphones", "backpack", "guitar", "trumpet", "keyboard", "computer", "telescope", "lantern", "balloon", "birthday", "graduation", "wedding",
          "desert", "jungle", "mountain", "volcano", "beach", "rainbow", "galaxy", "underwater", "snowman", "pumpkin", "christmas")
_INDEX = {w.lower(): w for w in PROPER + COMMON}


def fix_word(word: str) -> str:
    low = word.lower()
    if low[:-1] in _INDEX or low[:-2] in _INDEX:                     # "superheros", "camels": a plural of a known word is not a typo of the singular
        return word
    if len(low) < 6 or low in _INDEX or not low.isalpha():
        return _INDEX.get(low, word) if low in _INDEX and low in {p.lower() for p in PROPER} else word
    hit = difflib.get_close_matches(low, list(_INDEX), n=1, cutoff=0.85)
    return _INDEX[hit[0]] if hit else word


def fix_text(text: str) -> str:
    """Every word through fix_word; spacing and punctuation kept. "camel in lamborgini" -> "camel in Lamborghini"; "paris" -> "Paris"."""
    return re.sub(r"[A-Za-z]+(?:-[A-Za-z]+)?", lambda m: fix_word(m.group(0)), str(text or ""))


def changed(before: str, after: str) -> list[tuple[str, str]]:
    """[(typed, meant)] for the words fix_text changed (not a capital alone), so the card can say what was read."""
    a, b = re.findall(r"[A-Za-z]+(?:-[A-Za-z]+)?", before), re.findall(r"[A-Za-z]+(?:-[A-Za-z]+)?", after)
    return [(x, y) for x, y in zip(a, b) if x.lower() != y.lower()] if len(a) == len(b) else []
