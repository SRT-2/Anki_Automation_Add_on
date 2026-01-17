import requests
import re
import os
from config import *

PROCESSED_LOG = os.path.join("input", ".processed_files.txt")
PROCESSED_DIR = os.path.join("input", "_processed")
os.makedirs(PROCESSED_DIR, exist_ok=True)

def archive_processed_file(filename):
    src = os.path.join("input", filename)
    dst = os.path.join(PROCESSED_DIR, filename)

    if os.path.exists(src):
        os.replace(src, dst)  # atomic + safe on Windows



def load_processed_files():
    if not os.path.exists(PROCESSED_LOG):
        return set()

    with open(PROCESSED_LOG, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())


def mark_file_as_processed(filename):
    with open(PROCESSED_LOG, "a", encoding="utf-8") as f:
        f.write(filename + "\n")

def contains_cloze(text):
    return bool(re.search(r"{{c\d+::.*?}}", text or ""))


# NEW: Field sanitizer
def clean_field(text):
    if not text:
        return ""
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)  # remove **bold**
    text = re.sub(r"__(.*?)__", r"\1", text)      # remove __underline__
    return text.strip()

def parse_cards(text):
    cards = []

    current_front = None
    current_back_lines = []

    lines = text.replace("\r\n", "\n").split("\n")

    for line in lines:
        stripped = line.strip()

        # Skip separators like ---
        if stripped == "---":
            continue

        # FRONT
        front_match = re.match(
            r"(?:\*\*|__)?Front(?:\*\*|__)?\s*:\s*(.+)",
            stripped,
            re.IGNORECASE
        )
        if front_match:
            # Save previous card
            if current_front and current_back_lines:
                cards.append({
                    "type": "basic",
                    "front": current_front.strip(),
                    "back": "\n".join(current_back_lines).strip()
                })

            current_front = front_match.group(1)
            current_back_lines = []
            continue

        # BACK (start)
        back_match = re.match(
            r"(?:\*\*|__)?Back(?:\*\*|__)?\s*:\s*(.*)",
            stripped,
            re.IGNORECASE
        )
        if back_match:
            first_line = back_match.group(1)
            if first_line:
                current_back_lines.append(first_line)
            continue

        # CONTINUATION of Back
        if current_front and current_back_lines is not None:
            current_back_lines.append(line)

    # Save last card
    if current_front and current_back_lines:
        cards.append({
            "type": "basic",
            "front": current_front.strip(),
            "back": "\n".join(current_back_lines).strip()
        })

    return cards



def ensure_deck_exists(deck_name):
    payload = {
        "action": "createDeck",
        "version": 6,
        "params": {
            "deck": deck_name
        }
    }
    # createDeck is safe: it does nothing if deck already exists
    requests.post(ANKI_CONNECT_URL, json=payload)


def add_to_anki(cards, deck_name):
    notes = []

    for card in cards:
        # If card has cloze syntax anywhere → force Cloze model
        if card["type"] == "basic" and (
            contains_cloze(card.get("front")) or contains_cloze(card.get("back"))
        ):
            text = f"{card['front']}<br><br>{card['back']}"
            note = {
                "deckName": deck_name,
                "modelName": "Cloze",
                "fields": {
                    "Text": clean_field(text)
                },
                "tags": ["GPT", "auto-cloze"]
            }
            notes.append(note)

        # Normal Basic card
        elif card["type"] == "basic":
            note = {
                "deckName": deck_name,
                "modelName": "Basic",
                "fields": {
                    "Front": clean_field(card["front"]),
                    "Back": clean_field(card["back"])
                },
                "tags": ["GPT"]
            }
            notes.append(note)

        # Explicit Cloze card
        elif card["type"] == "cloze":
            note = {
                "deckName": deck_name,
                "modelName": "Cloze",
                "fields": {
                    "Text": clean_field(card["text"])
                },
                "tags": ["GPT", "cloze"]
            }
            notes.append(note)

    if not notes:
        print("ℹ️ No cards to add.")
        return None

    payload = {
        "action": "addNotes",
        "version": 6,
        "params": {"notes": notes}
    }

    res = requests.post(ANKI_CONNECT_URL, json=payload).json()
    print("🔹 AnkiConnect response:", res)
    return res



def detect_deck_name(text, filename):
    text = text.replace("\r\n", "\n")

    category = None
    topic = None

    # Highest priority: explicit Deck
    deck_match = re.search(r"Deck:\s*(.+)", text, re.IGNORECASE)
    if deck_match:
        return deck_match.group(1).strip()

    # Category + Topic
    cat_match = re.search(r"Category:\s*(.+)", text, re.IGNORECASE)
    if cat_match:
        category = cat_match.group(1).strip()

    topic_match = re.search(r"Topic:\s*(.+)", text, re.IGNORECASE)
    if topic_match:
        topic = topic_match.group(1).strip()

    if category and topic:
        return f"{category}::{topic}"

    # Fallback to filename
    return os.path.splitext(filename)[0]



def process_folder(folder="input"):
    results = []
    processed_files = load_processed_files()

    files = os.listdir(folder)
    print("📂 Files found:", files)

    for file in files:
        if not file.endswith(".txt") or file == ".processed_files.txt":
            continue


        if file in processed_files:
            print(f"⏭️ Skipping already processed file: {file}")
            continue

        path = os.path.join(folder, file)
        print(f"\n📄 Reading file: {path}")

        with open(path, encoding="utf-8") as f:
            text = f.read()

        print("📜 File preview:")
        print(text[:300])

        cards = parse_cards(text)
        print(f"🧠 Cards detected in {file}: {len(cards)}")

        if not cards:
            print("⚠️ Skipping file (no cards found)")
            continue

        raw_deck = detect_deck_name(text, file)
        deck_name = sanitize_deck_name(raw_deck)
        print(f"📦 Deck name: {deck_name}")

        results.append((deck_name, cards, file))

    return results


def sanitize_deck_name(name):
    if not name:
        return "GPT_Auto"

    # Remove markdown
    name = re.sub(r"\*\*(.*?)\*\*", r"\1", name)
    name = re.sub(r"__(.*?)__", r"\1", name)

    # Allow :: for hierarchy
    name = re.sub(r"[^\w\s:\-]", "", name)

    # Normalize spaces
    name = re.sub(r"\s+", " ", name)

    return name.strip()[:100]



if __name__ == "__main__":
    deck_batches = process_folder()

    if not deck_batches:
        print("ℹ️ No new files to process")
        exit()

    for deck_name, cards, filename in deck_batches:
        print(f"\n🚀 Processing deck: {deck_name}")

        ensure_deck_exists(deck_name)

        result = add_to_anki(cards, deck_name)

        if result and result.get("error") is None:
            mark_file_as_processed(filename)
            archive_processed_file(filename)
            print(f"📦 Archived file: {filename}")
        else:
            print(f"❌ Failed to process file: {filename}")

