#!/usr/bin/env python3
"""
Image Generator using OpenAI DALL-E 3
--------------------------------------
Reads prompts from JSON files and generates images using the OpenAI API.
Supports specifying orientation and output size via command-line arguments.
All libraries available via Debian 13 apt repository.

Install dependencies:
    sudo apt install python3-openai python3-pillow

Usage:
    export OPENAI_API_KEY="sk-your-key-here"

    # Default (landscape, no resize):
    python3 generate_images.py

    # Portrait orientation:
    python3 generate_images.py --orientation portrait

    # Square:
    python3 generate_images.py --orientation square

    # Resize output to a specific size in pixels (width x height):
    python3 generate_images.py --output-size 800x600

    # Portrait resized to A4-ish dimensions for printing:
    python3 generate_images.py --orientation portrait --output-size 2480x3508

    # Only process one specific JSON file:
    python3 generate_images.py --file silly-scene-prompts.json

    # HD quality, natural style:
    python3 generate_images.py --quality hd --style natural

    # Preview prompts without making API calls:
    python3 generate_images.py --dry-run

    # Full example:
    python3 generate_images.py --orientation portrait --output-size 1200x1600 --quality hd

DALL-E 3 native sizes:
    landscape  ->  1792x1024
    portrait   ->  1024x1792
    square     ->  1024x1024

--output-size resizes the saved image AFTER download using Pillow.
The API always generates at the native DALL-E size for the chosen orientation.

Prompt linking:
  Each output folder gets a manifest.json tracking every prompt -> image mapping.
  Each PNG also has the prompt embedded in its metadata, readable with:
    python3 -c "from PIL import Image; img=Image.open('file.png'); print(img.info)"
"""

import os
import json
import time
import datetime
import argparse
import urllib.request
from pathlib import Path
from openai import OpenAI
from PIL import Image, PngImagePlugin

# ─────────────────────────────────────────────
# DALL-E 3 VALID SIZES BY ORIENTATION
# ─────────────────────────────────────────────

ORIENTATION_SIZES = {
    "landscape": "1792x1024",
    "portrait":  "1024x1792",
    "square":    "1024x1024",
}

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

if not OPENAI_API_KEY:
    raise SystemExit(
        "\n[ERROR] No API key found.\n"
        "Set it with: export OPENAI_API_KEY='sk-your-key-here'\n"
    )

# JSON files and their output subfolder names
PROMPT_FILES = [
    {
        "file":       "silly-scene-prompts.json",
        "output_dir": "output_images/silly_scenes",
        "prompt_key": "prompt",
        "mode":       "silly",       # "Can you make me an image of ..."
    },
    {
        "file":       "writing-prompts.json",
        "output_dir": "output_images/writing_backgrounds",
        "prompt_key": "prompt",
        "mode":       "background",  # "Background image with space for text..."
    },
]

# Seconds to wait between API calls to avoid rate limiting
DELAY_BETWEEN_CALLS = 12

# ─────────────────────────────────────────────
# ARGUMENT PARSING
# ─────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate images from JSON prompt files using DALL-E 3.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--orientation",
        choices=["landscape", "portrait", "square"],
        default="landscape",
        help="Image orientation sent to DALL-E 3 (default: landscape)",
    )
    parser.add_argument(
        "--output-size",
        metavar="WxH",
        default=None,
        help=(
            "Resize saved image to WxH pixels after download, e.g. 800x600 or 2480x3508. "
            "Does not affect what is sent to the API."
        ),
    )
    parser.add_argument(
        "--quality",
        choices=["standard", "hd"],
        default="standard",
        help="DALL-E 3 image quality (default: standard). hd costs ~2x more.",
    )
    parser.add_argument(
        "--style",
        choices=["vivid", "natural"],
        default="vivid",
        help="DALL-E 3 image style (default: vivid).",
    )
    parser.add_argument(
        "--file",
        metavar="FILENAME",
        default=None,
        help="Only process a specific JSON file instead of all configured files.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print prompts that would be sent without making any API calls.",
    )
    return parser.parse_args()


def parse_output_size(size_str: str):
    """Parse a WxH string into a (width, height) tuple."""
    if not size_str:
        return None
    parts = size_str.lower().split("x")
    if len(parts) != 2:
        raise SystemExit(
            f"[ERROR] --output-size must be in WxH format, e.g. 800x600. Got: {size_str}"
        )
    try:
        w, h = int(parts[0]), int(parts[1])
    except ValueError:
        raise SystemExit(
            f"[ERROR] --output-size values must be integers. Got: {size_str}"
        )
    if w < 1 or h < 1:
        raise SystemExit(
            f"[ERROR] --output-size values must be positive. Got: {size_str}"
        )
    return (w, h)

# ─────────────────────────────────────────────
# PROMPT BUILDERS
# ─────────────────────────────────────────────

def build_silly_prompt(raw_prompt: str) -> str:
    return f"Can you make me an image of {raw_prompt}?"

def build_background_prompt(raw_prompt: str) -> str:
    return (
        f"Can you generate me a background image with space in the center for text. "
        f"The inspiration is '{raw_prompt}'"
    )

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def load_json(filepath: str):
    path = Path(filepath)
    if not path.exists():
        print(f"  [SKIP] File not found: {filepath}")
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def sanitize_filename(text: str, max_length: int = 40) -> str:
    """Turn a title into a safe filename slug."""
    keepchars = (" ", "_", "-")
    name = "".join(c if c.isalnum() or c in keepchars else "" for c in text)
    return name.strip().replace(" ", "_")[:max_length]

def download_image(url: str, dest_path: Path) -> bool:
    """Download image from URL to dest_path using stdlib urllib."""
    try:
        urllib.request.urlretrieve(url, dest_path)
        return True
    except Exception as e:
        print(f"  [ERROR] Failed to download image: {e}")
        return False

def embed_png_metadata(dest_path: Path, metadata: dict) -> bool:
    """
    Embed prompt and generation info into the PNG's tEXt chunks using Pillow.
    These are preserved in the file and readable by any PNG-aware tool.
    Read them back with:
        from PIL import Image
        print(Image.open("file.png").info)
    """
    try:
        with Image.open(dest_path) as img:
            png_info = PngImagePlugin.PngInfo()
            for key, value in metadata.items():
                png_info.add_text(key, str(value))
            img.save(dest_path, pnginfo=png_info)
        return True
    except Exception as e:
        print(f"  [WARN] Metadata embedding failed: {e}")
        return False

def resize_image(src_path: Path, output_size: tuple) -> bool:
    """Resize image at src_path in-place using Pillow (high quality Lanczos).
    Note: resizing reloads the file so metadata must be embedded AFTER resizing."""
    try:
        with Image.open(src_path) as img:
            # Preserve any existing PNG metadata through the resize
            existing_info = img.info
            resized = img.resize(output_size, Image.LANCZOS)
            png_info = PngImagePlugin.PngInfo()
            for key, value in existing_info.items():
                if isinstance(value, str):
                    png_info.add_text(key, value)
            resized.save(src_path, pnginfo=png_info)
        return True
    except Exception as e:
        print(f"  [ERROR] Resize failed: {e}")
        return False

def load_manifest(manifest_path: Path) -> dict:
    """Load existing manifest or return a fresh one."""
    if manifest_path.exists():
        with open(manifest_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "generated_by": "generate_images.py / DALL-E 3",
        "images": []
    }

def save_manifest(manifest_path: Path, manifest: dict):
    """Write manifest to disk, sorted by prompt_id."""
    manifest["images"].sort(key=lambda x: x.get("prompt_id", 0))
    manifest["last_updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

def upsert_manifest_entry(manifest: dict, entry: dict):
    """Add or replace a manifest entry matched by filename."""
    manifest["images"] = [
        e for e in manifest["images"]
        if e.get("filename") != entry["filename"]
    ]
    manifest["images"].append(entry)

def print_section(title: str):
    print()
    print("=" * 60)
    print(f"  {title}")
    print("=" * 60)

# ─────────────────────────────────────────────
# CORE GENERATION LOGIC
# ─────────────────────────────────────────────

def generate_images_from_file(
    client,
    config:      dict,
    api_size:    str,
    output_size,
    quality:     str,
    style:       str,
    orientation: str,
    dry_run:     bool,
):
    data = load_json(config["file"])
    if not data:
        return

    output_dir    = Path(config["output_dir"])
    manifest_path = output_dir / "manifest.json"

    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest = load_manifest(manifest_path)
    else:
        manifest = {}

    prompts = data.get("prompts", [])
    mode    = config["mode"]
    key     = config["prompt_key"]

    saved_size = f"{output_size[0]}x{output_size[1]}" if output_size else api_size
    size_label = (
        f"API: {api_size}  ->  saved as: {saved_size}"
        if output_size else
        f"API: {api_size}  (no resize)"
    )

    print_section(f"{data.get('title', config['file'])}  ({len(prompts)} prompts)")
    print(f"  Output folder : {output_dir}")
    print(f"  Manifest      : {manifest_path}")
    print(f"  Size          : {size_label}")
    print(f"  Quality       : {quality}  |  Style: {style}")
    if dry_run:
        print("  *** DRY RUN — no API calls will be made ***")
    print()

    for idx, item in enumerate(prompts):
        prompt_id  = item.get("id", idx + 1)
        title      = item.get("title", f"prompt_{prompt_id}")
        raw_prompt = item.get(key, "")
        emoji      = item.get("emoji", "")
        theme      = item.get("theme", "")

        if mode == "silly":
            full_prompt = build_silly_prompt(raw_prompt)
        elif mode == "background":
            full_prompt = build_background_prompt(raw_prompt)
        else:
            full_prompt = raw_prompt

        filename  = f"{prompt_id:02d}_{sanitize_filename(title)}.png"
        dest_path = output_dir / filename

        print(f"  [{prompt_id:02d}] {emoji} {title}")
        print(f"        Prompt : {full_prompt[:90]}{'...' if len(full_prompt) > 90 else ''}")

        if dry_run:
            print(f"        [DRY RUN] Would save to: {dest_path}\n")
            continue

        # Skip if already generated
        if dest_path.exists():
            print(f"        [SKIP] Already exists: {filename}\n")
            continue

        try:
            response = client.images.generate(
                model="dall-e-3",
                prompt=full_prompt,
                size=api_size,
                quality=quality,
                style=style,
                n=1,
            )

            image_url     = response.data[0].url
            # DALL-E 3 may rewrite the prompt; capture what was actually used
            revised_prompt = getattr(response.data[0], "revised_prompt", full_prompt)

            if not download_image(image_url, dest_path):
                print(f"        [ERROR]   Download failed for prompt {prompt_id}\n")
                continue

            # ── Resize first (before embedding metadata) ──────────────
            if output_size:
                if resize_image(dest_path, output_size):
                    print(f"        [SIZED]   Resized to {saved_size}")
                else:
                    print(f"        [WARN]    Resize failed, original size kept")

            # ── Embed metadata into PNG tEXt chunks ───────────────────
            generated_at = datetime.datetime.now().isoformat(timespec="seconds")
            png_metadata = {
                "prompt_id":      str(prompt_id),
                "title":          title,
                "theme":          theme,
                "raw_prompt":     raw_prompt,
                "full_prompt":    full_prompt,
                "revised_prompt": revised_prompt,
                "source_file":    config["file"],
                "orientation":    orientation,
                "api_size":       api_size,
                "saved_size":     saved_size,
                "quality":        quality,
                "style":          style,
                "generated_at":   generated_at,
            }
            if embed_png_metadata(dest_path, png_metadata):
                print(f"        [META]    Prompt embedded in PNG metadata")

            print(f"        [SAVED]   {dest_path}")

            # ── Update manifest ────────────────────────────────────────
            manifest_entry = {
                "prompt_id":      prompt_id,
                "title":          title,
                "theme":          theme,
                "emoji":          emoji,
                "filename":       filename,
                "filepath":       str(dest_path),
                "raw_prompt":     raw_prompt,
                "full_prompt":    full_prompt,
                "revised_prompt": revised_prompt,
                "source_file":    config["file"],
                "orientation":    orientation,
                "api_size":       api_size,
                "saved_size":     saved_size,
                "quality":        quality,
                "style":          style,
                "generated_at":   generated_at,
            }
            upsert_manifest_entry(manifest, manifest_entry)
            save_manifest(manifest_path, manifest)
            print(f"        [MANIFEST] {manifest_path} updated")

        except Exception as e:
            print(f"        [ERROR]   API call failed: {e}")

        print()

        # Rate limit buffer — skip delay after the last item
        if idx < len(prompts) - 1:
            print(f"        Waiting {DELAY_BETWEEN_CALLS}s before next call...")
            time.sleep(DELAY_BETWEEN_CALLS)


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

def main():
    args = parse_args()

    api_size    = ORIENTATION_SIZES[args.orientation]
    output_size = parse_output_size(args.output_size) if args.output_size else None
    client      = OpenAI(api_key=OPENAI_API_KEY)

    # Filter to a specific file if --file was passed
    files_to_process = PROMPT_FILES
    if args.file:
        files_to_process = [c for c in PROMPT_FILES if c["file"] == args.file]
        if not files_to_process:
            raise SystemExit(
                f"[ERROR] '{args.file}' not found in configured PROMPT_FILES.\n"
                f"Available: {[c['file'] for c in PROMPT_FILES]}"
            )

    size_display = (
        f"{api_size} -> resized to {args.output_size}"
        if output_size else api_size
    )

    print(f"\n🎨 OpenAI DALL-E 3 Image Generator")
    print(f"   Orientation : {args.orientation}  ({size_display})")
    print(f"   Quality     : {args.quality}  |  Style: {args.style}")
    if args.dry_run:
        print("   *** DRY RUN MODE — no images will be generated ***")

    for config in files_to_process:
        generate_images_from_file(
            client=client,
            config=config,
            api_size=api_size,
            output_size=output_size,
            quality=args.quality,
            style=args.style,
            orientation=args.orientation,
            dry_run=args.dry_run,
        )

    print_section("Done!")
    if not args.dry_run:
        print("  Images and manifests saved to:")
        for config in files_to_process:
            print(f"    -> {config['output_dir']}/")
            print(f"       manifest.json")
    print()


if __name__ == "__main__":
    main()
