#! /usr/bin/python3 
import json
import os

# ─────────────────────────────────────────────
# CONFIGURATION — update paths if needed
# ─────────────────────────────────────────────
SILLY_SCENES_FILE = "silly-scene-prompts.json"
WRITING_PROMPTS_FILE = "writing-prompts.json"


def load_json(filepath):
    if not os.path.exists(filepath):
        print(f"  [ERROR] File not found: {filepath}\n")
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def print_section(title):
    print()
    print("=" * 60)
    print(f"  {title}")
    print("=" * 60)


# ─────────────────────────────────────────────
# PART 1: Silly scene image prompts
# ─────────────────────────────────────────────
print_section("SILLY SCENE IMAGE PROMPTS")

silly_data = load_json(SILLY_SCENES_FILE)
if silly_data:
    for item in silly_data["prompts"]:
        print(f"\n{item['emoji']} Prompt {item['id']}: {item['title']}")
        print(f"  Can you make me an image of {item['prompt']}?")


# ─────────────────────────────────────────────
# PART 2: Writing prompt background images
# ─────────────────────────────────────────────
print_section("WRITING PROMPT BACKGROUND IMAGES")

writing_data = load_json(WRITING_PROMPTS_FILE)
if writing_data:
    for item in writing_data["prompts"]:
        print(f"\n{item['emoji']} Prompt {item['id']}: {item['title']}")
        print(
            f"  Can you generate me a background image with space in the center for text. "
            f"The inspiration is '{item['prompt']}'"
        )

print()
print("=" * 60)
print(f"  Done! Generated prompts from both files.")
print("=" * 60)
print()
