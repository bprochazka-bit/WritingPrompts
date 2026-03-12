#!/usr/bin/env python3
"""Generate PDF with all 40 prompts - one prompt per page."""

import json
import os
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.colors import Color, white, black, HexColor
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader
from PIL import Image

BASE = "/home/user/WritingPrompts"
WIDTH, HEIGHT = letter  # 612 x 792 points

# Register fonts
pdfmetrics.registerFont(TTFont("LibSans", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"))
pdfmetrics.registerFont(TTFont("LibSansBold", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"))
pdfmetrics.registerFont(TTFont("LibSansItalic", "/usr/share/fonts/truetype/liberation/LiberationSans-Italic.ttf"))
pdfmetrics.registerFont(TTFont("LibSansBoldItalic", "/usr/share/fonts/truetype/liberation/LiberationSans-BoldItalic.ttf"))
pdfmetrics.registerFont(TTFont("LibSerif", "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf"))
pdfmetrics.registerFont(TTFont("LibSerifBold", "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf"))
pdfmetrics.registerFont(TTFont("LibSerifItalic", "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf"))
pdfmetrics.registerFont(TTFont("LibSerifBoldItalic", "/usr/share/fonts/truetype/liberation/LiberationSerif-BoldItalic.ttf"))


def wrap_text(c, text, font, size, max_width):
    """Word-wrap text to fit within max_width. Returns list of lines."""
    c.setFont(font, size)
    words = text.split()
    lines = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        if c.stringWidth(test, font, size) <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def draw_text_with_shadow(c, text, x, y, font, size, shadow_offset=2, text_color=white, shadow_color=None):
    """Draw text with a soft, diffuse drop shadow."""
    c.setFont(font, size)
    # Draw multiple shadow layers at increasing offsets for a soft diffuse look
    layers = [
        (shadow_offset * 0.3, 0.10),
        (shadow_offset * 0.6, 0.12),
        (shadow_offset * 1.0, 0.15),
        (shadow_offset * 1.4, 0.18),
        (shadow_offset * 1.8, 0.15),
        (shadow_offset * 2.2, 0.12),
        (shadow_offset * 2.8, 0.08),
    ]
    for offset_mult, alpha in layers:
        c.setFillColor(Color(0, 0, 0, alpha))
        c.drawString(x + offset_mult, y - offset_mult, text)
    c.setFillColor(text_color)
    c.drawString(x, y, text)


def find_image_for_prompt(folder, prompt_id):
    """Find the image file that starts with the prompt number."""
    img_dir = os.path.join(BASE, folder)
    for f in os.listdir(img_dir):
        if f.startswith(f"{prompt_id}-") and f.lower().endswith(".png"):
            return os.path.join(img_dir, f)
    return None


def draw_writing_prompt_page(c, prompt):
    """Writing prompt: image background with text overlay and drop shadow."""
    img_path = find_image_for_prompt("writing-prompts", prompt["id"])
    if not img_path:
        return

    img = Image.open(img_path)
    iw, ih = img.size
    # Use landscape page for landscape images to avoid cropping
    if iw > ih:
        pw, ph = HEIGHT, WIDTH  # swap to landscape
        c.setPageSize((pw, ph))
    else:
        pw, ph = WIDTH, HEIGHT
        c.setPageSize((pw, ph))
    scale = max(pw / iw, ph / ih)
    draw_w = iw * scale
    draw_h = ih * scale
    x_off = (pw - draw_w) / 2
    y_off = (ph - draw_h) / 2
    c.drawImage(ImageReader(img), x_off, y_off, draw_w, draw_h)

    # Dark overlay for readability
    c.setFillColor(Color(0, 0, 0, 0.45))
    c.rect(0, 0, pw, ph, fill=1, stroke=0)

    margin = 0.75 * inch
    usable = pw - 2 * margin

    # Title
    y = ph - 1.2 * inch
    title = prompt["title"]
    title_font = "LibSansBold"
    title_size = 34
    title_lines = wrap_text(c, title, title_font, title_size, usable)
    for line in title_lines:
        draw_text_with_shadow(c, line, margin, y, title_font, title_size, shadow_offset=3)
        y -= title_size + 8

    y -= 20

    # Prompt text
    prompt_font = "LibSans"
    prompt_size = 21
    prompt_lines = wrap_text(c, prompt["prompt"], prompt_font, prompt_size, usable)
    for line in prompt_lines:
        draw_text_with_shadow(c, line, margin, y, prompt_font, prompt_size, shadow_offset=2)
        y -= prompt_size + 7


def draw_silly_scene_page(c, prompt):
    """Silly scene: just the image, full page."""
    img_path = find_image_for_prompt("silly-scene-prompts", prompt["id"])
    if not img_path:
        return

    img = Image.open(img_path)
    iw, ih = img.size
    # Use landscape page for landscape images to avoid cropping
    if iw > ih:
        pw, ph = HEIGHT, WIDTH  # swap to landscape
        c.setPageSize((pw, ph))
    else:
        pw, ph = WIDTH, HEIGHT
        c.setPageSize((pw, ph))
    scale = max(pw / iw, ph / ih)
    draw_w = iw * scale
    draw_h = ih * scale
    x_off = (pw - draw_w) / 2
    y_off = (ph - draw_h) / 2
    c.drawImage(ImageReader(img), x_off, y_off, draw_w, draw_h)


def draw_reallife_prompt_page(c, prompt):
    """Real-life writing prompt: warm, journal-style design with vertically centered content."""
    c.setPageSize((WIDTH, HEIGHT))
    # Background: warm cream/parchment
    bg = HexColor("#FFF8E7")
    c.setFillColor(bg)
    c.rect(0, 0, WIDTH, HEIGHT, fill=1, stroke=0)

    margin = 0.75 * inch
    usable = WIDTH - 2 * margin
    accent = HexColor("#C8553D")

    # Decorative top border
    c.setStrokeColor(accent)
    c.setLineWidth(3)
    y_border_top = HEIGHT - 0.5 * inch
    c.line(margin, y_border_top, WIDTH - margin, y_border_top)

    # Bottom border
    c.setStrokeColor(accent)
    c.setLineWidth(3)
    c.line(margin, 0.5 * inch, WIDTH - margin, 0.5 * inch)

    # Pre-calculate content height
    title_font = "LibSerifBold"
    title_size = 34
    title_lines = wrap_text(c, prompt["title"], title_font, title_size, usable)
    title_h = len(title_lines) * (title_size + 10)

    divider_h = 40  # space for divider

    # Theme badge
    theme_font = "LibSansItalic"
    theme_size = 16
    theme_h = theme_size + 20

    prompt_font = "LibSerif"
    prompt_size = 20
    prompt_lines = wrap_text(c, prompt["prompt"], prompt_font, prompt_size, usable - 20)
    prompt_h = len(prompt_lines) * (prompt_size + 9)

    total_h = theme_h + title_h + divider_h + prompt_h
    available = (y_border_top - 20) - (0.5 * inch + 20)
    y_start = 0.5 * inch + 20 + (available + total_h) / 2

    # Theme label
    y = y_start
    c.setFillColor(HexColor("#8B6914"))
    c.setFont(theme_font, theme_size)
    theme_text = f"Real Life Writing Prompt  //  {prompt.get('theme', '')}"
    tw = c.stringWidth(theme_text, theme_font, theme_size)
    c.drawString((WIDTH - tw) / 2, y, theme_text)
    y -= theme_h

    # Title
    c.setFillColor(HexColor("#2C1810"))
    for line in title_lines:
        lw = c.stringWidth(line, title_font, title_size)
        c.setFont(title_font, title_size)
        c.drawString((WIDTH - lw) / 2, y, line)
        y -= title_size + 10

    # Divider
    y -= 10
    c.setStrokeColor(accent)
    c.setLineWidth(1.5)
    c.line(WIDTH / 2 - 100, y, WIDTH / 2 + 100, y)
    y -= 30

    # Prompt text
    c.setFillColor(HexColor("#3C2415"))
    for line in prompt_lines:
        c.setFont(prompt_font, prompt_size)
        c.drawString(margin + 10, y, line)
        y -= prompt_size + 9


def draw_story_starter_page(c, prompt):
    """Story starter: dramatic, adventure-style design with vertically centered content."""
    c.setPageSize((WIDTH, HEIGHT))
    # Background: dark navy blue
    bg = HexColor("#1A1A2E")
    c.setFillColor(bg)
    c.rect(0, 0, WIDTH, HEIGHT, fill=1, stroke=0)

    margin = 0.75 * inch
    usable = WIDTH - 2 * margin
    accent = HexColor("#E2B714")

    # Top border
    c.setStrokeColor(accent)
    c.setLineWidth(3)
    y_border_top = HEIGHT - 0.5 * inch
    c.line(margin, y_border_top, WIDTH - margin, y_border_top)

    # Bottom border
    c.setStrokeColor(accent)
    c.setLineWidth(3)
    c.line(margin, 0.5 * inch, WIDTH - margin, 0.5 * inch)

    # Pre-calculate content height
    theme_font = "LibSansItalic"
    theme_size = 16
    theme_h = theme_size + 20

    title_font = "LibSansBold"
    title_size = 34
    title_lines = wrap_text(c, prompt["title"], title_font, title_size, usable)
    title_h = len(title_lines) * (title_size + 10)

    divider_h = 45

    opening_font = "LibSerifBoldItalic"
    opening_size = 20
    opening_lines = wrap_text(c, prompt["opening"], opening_font, opening_size, usable - 20)
    opening_h = len(opening_lines) * (opening_size + 9)

    instruction_h = 50

    total_h = theme_h + title_h + divider_h + opening_h + instruction_h
    available = (y_border_top - 20) - (0.5 * inch + 20)
    y_start = 0.5 * inch + 20 + (available + total_h) / 2

    # Theme label
    y = y_start
    c.setFillColor(HexColor("#7B8794"))
    c.setFont(theme_font, theme_size)
    theme_text = f"Story Starter  //  {prompt.get('theme', '')}"
    tw = c.stringWidth(theme_text, theme_font, theme_size)
    c.drawString((WIDTH - tw) / 2, y, theme_text)
    y -= theme_h

    # Title
    c.setFillColor(white)
    for line in title_lines:
        lw = c.stringWidth(line, title_font, title_size)
        c.setFont(title_font, title_size)
        c.drawString((WIDTH - lw) / 2, y, line)
        y -= title_size + 10

    # Divider
    y -= 10
    c.setStrokeColor(accent)
    c.setLineWidth(2)
    c.line(WIDTH / 2 - 100, y, WIDTH / 2 + 100, y)
    y -= 35

    # Opening sentences in bold italic
    c.setFillColor(HexColor("#E8D5B7"))
    for line in opening_lines:
        c.setFont(opening_font, opening_size)
        c.drawString(margin + 10, y, line)
        y -= opening_size + 9

    # "Keep writing..." instruction
    y -= 20
    c.setFillColor(accent)
    c.setFont("LibSansBoldItalic", 18)
    instruction = "Keep writing for one full page!"
    iw = c.stringWidth(instruction, "LibSansBoldItalic", 18)
    c.drawString((WIDTH - iw) / 2, y, instruction)


def main():
    output = os.path.join(BASE, "all_prompts.pdf")
    c = canvas.Canvas(output, pagesize=letter)

    # Load all prompts
    with open(os.path.join(BASE, "writing-prompts.json")) as f:
        writing = json.load(f)["prompts"]
    with open(os.path.join(BASE, "silly-scene-prompts.json")) as f:
        silly = json.load(f)["prompts"]
    with open(os.path.join(BASE, "reallife-writing-prompts.json")) as f:
        reallife = json.load(f)["prompts"]
    with open(os.path.join(BASE, "story-starter-prompts.json")) as f:
        starters = json.load(f)["prompts"]

    # All 10 writing prompts (image + text overlay)
    for p in writing:
        draw_writing_prompt_page(c, p)
        c.showPage()

    # All 10 silly scenes (image only)
    for p in silly:
        draw_silly_scene_page(c, p)
        c.showPage()

    # All 10 real-life writing prompts (styled text)
    for p in reallife:
        draw_reallife_prompt_page(c, p)
        c.showPage()

    # All 10 story starters (styled text)
    for p in starters:
        draw_story_starter_page(c, p)
        c.showPage()

    c.save()
    print(f"All 40 prompts saved to: {output}")


if __name__ == "__main__":
    main()
