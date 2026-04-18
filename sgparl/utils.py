import re

import nltk
import pandas as pd


def _ensure_nltk_data():
    """Download punkt tokenizer if not already present."""
    try:
        nltk.data.find("tokenizers/punkt_tab")
    except LookupError:
        nltk.download("punkt_tab", quiet=True)


def get_mp_name(x):
    """Extract clean MP name from raw speaker string.

    Ported from transform/__init__.py.
    """
    if pd.isna(x) if not isinstance(x, str) else x == "":
        return ""
    if "SPEAKER" in x:
        temp = re.search(r"\(([^()]+)\(", x)
        if temp:
            match = re.sub(r"^(?:Mr|Mrs|Miss|Mdm|Ms|Dr|Prof)\s+", "", temp.group(1))
            return match.strip()
        else:
            return ""
    else:
        match = re.search(r"(?:Mr|Mrs|Miss|Mdm|Ms|Dr|Prof)\s+([\w\s-]+)", x)
        if match:
            return match.group(1).strip()
        else:
            return ""


def count_syllables(word):
    """Count syllables in a word using vowel-group heuristic.

    Ported from transform/speeches.py.
    """
    vowels = "aeiouy"
    word = word.lower()
    count = 0
    prev_char_was_vowel = False

    for char in word:
        if char in vowels:
            if not prev_char_was_vowel:
                count += 1
            prev_char_was_vowel = True
        else:
            prev_char_was_vowel = False

    if word.endswith(("e", "es", "ed")) and not word.endswith(("le", "ble", "ple")):
        count -= 1
    if count == 0:
        count = 1

    return count


def calc_number_of_syllables(text):
    """Calculate total syllables in a text string."""
    _ensure_nltk_data()
    words = nltk.word_tokenize(text)
    return sum(count_syllables(word) for word in words)


def calc_number_of_sentences(text):
    """Count sentences by splitting on sentence-ending punctuation."""
    sentences = re.split(r"[.!?]+", text)
    sentences = [s for s in sentences if s.strip()]
    return max(len(sentences), 1)


def count_words_and_characters(text):
    """Return (num_words, num_characters) for a text string."""
    words = text.split()
    num_words = len(words)
    num_characters = len(re.findall(r"[a-zA-Z]", text))
    return num_words, num_characters
