#!/usr/bin/env python3
"""Web UI for laying out writing prompt text over background images.

Drag and resize title/body text boxes on each page, then generate a PDF.
PDF generation uses cairosvg to render the exact same SVG as the editor —
what you see is what you get.

Usage:
    python3 layout_editor.py [--port 5000] [--prompts writing-prompts.json]
"""

import argparse
import json
import os
import glob
import html
from io import BytesIO

from flask import Flask, request, jsonify, send_file, send_from_directory
from PIL import Image
import cairosvg
from pypdf import PdfWriter, PdfReader

app = Flask(__name__)

# ── Constants ──────────────────────────────────────────────────────────────
PW, PH = 612, 792  # US Letter in points
PROMPTS_FILE = "writing-prompts.json"
IMAGE_DIR = "writing-prompts"


# ── Helpers ────────────────────────────────────────────────────────────────
def _find_image(prompt_id):
    pattern = os.path.join(IMAGE_DIR, f"{prompt_id}-*")
    matches = glob.glob(pattern)
    return matches[0] if matches else None


def _load_prompts():
    with open(PROMPTS_FILE) as f:
        return json.load(f)


def _img_geometry(iw, ih, pw=PW, ph=PH):
    """Compute how the image covers the page. Returns (drawW, drawH, extraX, extraY)."""
    scale = max(pw / iw, ph / ih)
    dw, dh = iw * scale, ih * scale
    return dw, dh, dw - pw, dh - ph


def _esc(s):
    """Escape text for SVG/XML."""
    return html.escape(s, quote=True)


def _wrap_text_svg(text, font_size, max_width, is_bold):
    """Word-wrap text using approximate character widths for Liberation Sans."""
    char_w = font_size * (0.58 if is_bold else 0.52)
    words = text.split()
    lines = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip() if current else word
        if len(test) * char_w > max_width and current:
            lines.append(current)
            current = word
        else:
            current = test
    if current:
        lines.append(current)
    return lines


def _build_text_svg(text, box, font_size, is_bold, align, text_color,
                    shadow_color, shadow_dx, shadow_dy, shadow_blur,
                    pw=PW, ph=PH, filter_id=""):
    """Generate SVG elements for a text box (no box background, just text + shadow)."""
    x = box["x"] * pw
    y_top = box["y"] * ph
    w = box["w"] * pw
    h = box["h"] * ph
    pad = 8
    line_h = font_size * 1.25

    lines = _wrap_text_svg(text, font_size, w - 2 * pad, is_bold)

    anchor = "start"
    tx = x + pad
    if align == "center":
        anchor = "middle"
        tx = x + w / 2
    elif align == "right":
        anchor = "end"
        tx = x + w - pad

    weight = "bold" if is_bold else "normal"
    has_shadow = shadow_dx or shadow_dy or shadow_blur
    svg = ""

    # Shadow pass (optionally blurred)
    if has_shadow:
        if shadow_blur and filter_id:
            svg += f'<g filter="url(#{filter_id})">\n'
        ty = y_top + font_size + pad
        for line in lines:
            if ty > y_top + h - 4:
                break
            escaped = _esc(line)
            svg += (f'<text x="{tx + shadow_dx}" y="{ty + shadow_dy}" '
                    f'font-size="{font_size}" font-weight="{weight}" '
                    f'font-family="Liberation Sans, Arial, sans-serif" '
                    f'text-anchor="{anchor}" fill="{shadow_color}">'
                    f'{escaped}</text>\n')
            ty += line_h
        if shadow_blur and filter_id:
            svg += '</g>\n'

    # Main text pass
    ty = y_top + font_size + pad
    for line in lines:
        if ty > y_top + h - 4:
            break
        escaped = _esc(line)
        svg += (f'<text x="{tx}" y="{ty}" '
                f'font-size="{font_size}" font-weight="{weight}" '
                f'font-family="Liberation Sans, Arial, sans-serif" '
                f'text-anchor="{anchor}" fill="{text_color}">'
                f'{escaped}</text>\n')
        ty += line_h
    return svg


def build_page_svg(layout, prompt, img_src, editor_mode=False):
    """Build the SVG for one page.

    img_src: image URL or file:// path
    editor_mode: if True, uses unitless width/height for browser scaling;
                 if False (PDF), uses 'pt' units so cairosvg produces correct page size.
    """
    pw = layout.get("page_w", PW)
    ph = layout.get("page_h", PH)
    iw = layout.get("img_w", 0)
    ih = layout.get("img_h", 0)
    overlay = layout.get("overlay_opacity", 0.45)

    if editor_mode:
        # Browser: unitless dims get scaled via CSS; viewBox controls coordinate space
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" '
               f'viewBox="0 0 {pw} {ph}">\n')
    else:
        # PDF: explicit 'pt' units so cairosvg produces the correct page size
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" '
               f'width="{pw}pt" height="{ph}pt" viewBox="0 0 {pw} {ph}">\n')

    # Blur filters for shadows
    title_blur = layout.get("title_shadow_blur", 0)
    body_blur = layout.get("body_shadow_blur", 0)
    defs = f'<clipPath id="page-clip"><rect x="0" y="0" width="{pw}" height="{ph}"/></clipPath>\n'
    if title_blur > 0:
        defs += f'<filter id="title-blur"><feGaussianBlur stdDeviation="{title_blur}"/></filter>\n'
    if body_blur > 0:
        defs += f'<filter id="body-blur"><feGaussianBlur stdDeviation="{body_blur}"/></filter>\n'
    svg += f'<defs>{defs}</defs>\n'

    # Background image
    if img_src and iw and ih:
        dw, dh, ex, ey = _img_geometry(iw, ih, pw, ph)
        ox = layout.get("img_offset_x", 0.5)
        oy = layout.get("img_offset_y", 0.5)
        img_x = -ex * ox
        img_y = -ey * oy
        svg += '<g clip-path="url(#page-clip)">\n'
        svg += (f'<image href="{img_src}" x="{img_x}" y="{img_y}" '
                f'width="{dw}" height="{dh}" preserveAspectRatio="none"/>\n')
        svg += '</g>\n'
        svg += f'<rect x="0" y="0" width="{pw}" height="{ph}" fill="rgba(0,0,0,{overlay})"/>\n'

    # Editor-only: clickable background for panning
    if editor_mode and img_src:
        svg += (f'<rect class="bg-drag" x="0" y="0" width="{pw}" height="{ph}" '
                f'fill="transparent" style="cursor: grab;"/>\n')

    # Editor-only: margin guides (1" = 72pt)
    if editor_mode:
        svg += (f'<rect x="72" y="72" width="{pw - 144}" height="{ph - 144}" '
                f'fill="none" stroke="rgba(255,255,255,0.15)" stroke-width="0.5" '
                f'stroke-dasharray="4 4"/>\n')

    # Text settings (per-element shadow)
    title_color = layout.get("title_color", "#ffffff")
    body_color = layout.get("body_color", "#ffffff")

    # Title text
    svg += _build_text_svg(
        layout.get("title_text", prompt["title"]),
        layout["title_box"],
        layout.get("title_size", 34),
        True,
        layout.get("title_align", "left"),
        title_color,
        layout.get("title_shadow_color", "#000000"),
        layout.get("title_shadow_dx", 2),
        layout.get("title_shadow_dy", 2),
        title_blur,
        pw, ph,
        "title-blur" if title_blur > 0 else "",
    )

    # Body text
    svg += _build_text_svg(
        layout.get("body_text", prompt["prompt"]),
        layout["body_box"],
        layout.get("body_size", 21),
        False,
        layout.get("body_align", "left"),
        body_color,
        layout.get("body_shadow_color", "#000000"),
        layout.get("body_shadow_dx", 2),
        layout.get("body_shadow_dy", 2),
        body_blur,
        pw, ph,
        "body-blur" if body_blur > 0 else "",
    )

    svg += '</svg>'
    return svg


# ── Routes ─────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return HTML_PAGE


@app.route("/api/prompts")
def api_prompts():
    data = _load_prompts()
    result = []
    for p in data["prompts"]:
        img_path = _find_image(p["id"])
        iw, ih = 0, 0
        if img_path:
            img = Image.open(img_path)
            iw, ih = img.size
        result.append({
            "id": p["id"],
            "title": p["title"],
            "prompt": p["prompt"],
            "theme": p["theme"],
            "has_image": img_path is not None,
            "img_w": iw,
            "img_h": ih,
        })
    return jsonify(result)


@app.route("/api/image/<int:prompt_id>")
def api_image(prompt_id):
    img_path = _find_image(prompt_id)
    if not img_path:
        return "Not found", 404
    return send_from_directory(".", img_path)


@app.route("/api/generate-pdf", methods=["POST"])
def api_generate_pdf():
    """Generate PDF by rendering each page as SVG then converting with cairosvg."""
    data = request.get_json()
    layouts = data.get("layouts", [])
    prompts_data = {p["id"]: p for p in _load_prompts()["prompts"]}

    writer = PdfWriter()

    for layout in layouts:
        pid = layout["id"]
        prompt = prompts_data.get(pid)
        if not prompt:
            continue

        img_path = _find_image(pid)
        if not img_path:
            continue

        # Use file:// URL so cairosvg can load the image
        img_abs = os.path.abspath(img_path)
        img_src = f"file://{img_abs}"

        # Get image dimensions
        img = Image.open(img_path)
        layout["img_w"] = img.size[0]
        layout["img_h"] = img.size[1]

        svg_str = build_page_svg(layout, prompt, img_src, editor_mode=False)

        # Convert SVG to PDF
        pdf_buf = BytesIO()
        cairosvg.svg2pdf(bytestring=svg_str.encode("utf-8"),
                         write_to=pdf_buf, unsafe=True)
        pdf_buf.seek(0)
        reader = PdfReader(pdf_buf)
        writer.add_page(reader.pages[0])

    out_buf = BytesIO()
    writer.write(out_buf)
    out_buf.seek(0)
    return send_file(out_buf, mimetype="application/pdf",
                     download_name="writing_prompts.pdf")


# ── HTML/JS/CSS (single-page app) ─────────────────────────────────────────
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Writing Prompt Layout Editor</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: 'Liberation Sans', Arial, sans-serif; background: #1a1a2e; color: #eee; }
#app { display: flex; height: 100vh; }

/* Sidebar */
#sidebar { width: 260px; background: #16213e; overflow-y: auto; flex-shrink: 0; border-right: 1px solid #333; }
#sidebar h2 { padding: 16px; font-size: 16px; border-bottom: 1px solid #333; }
.prompt-item { padding: 10px 16px; cursor: pointer; border-bottom: 1px solid #222; font-size: 13px; display: flex; align-items: center; gap: 8px; }
.prompt-item:hover { background: #1a3a5c; }
.prompt-item.active { background: #0f3460; }
.prompt-item .dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.prompt-item .dot.has-img { background: #4ade80; }
.prompt-item .dot.no-img { background: #f87171; }

/* Main area */
#main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.toolbar-row { padding: 5px 12px; background: #16213e; border-bottom: 1px solid #333; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.toolbar-row button { padding: 4px 10px; border: none; border-radius: 4px; cursor: pointer; font-size: 11px; font-weight: bold; }
.toolbar-row .btn-primary { background: #3b82f6; color: #fff; }
.toolbar-row .btn-primary:hover { background: #2563eb; }
.toolbar-row .btn-success { background: #22c55e; color: #fff; }
.toolbar-row .btn-success:hover { background: #16a34a; }
.toolbar-row .btn-nav { background: #475569; color: #fff; }
.toolbar-row .btn-nav:hover { background: #64748b; }
.toolbar-row .btn-warn { background: #f59e0b; color: #000; }
.toolbar-row .btn-warn:hover { background: #d97706; }
.toolbar-row label { font-size: 11px; display: flex; align-items: center; gap: 3px; }
.toolbar-row input[type=number] { width: 42px; padding: 2px 3px; background: #1e293b; color: #eee; border: 1px solid #444; border-radius: 3px; font-size: 11px; }
.toolbar-row input[type=color] { width: 24px; height: 22px; padding: 0; border: 1px solid #444; border-radius: 3px; cursor: pointer; background: none; }
.toolbar-row .page-info { font-size: 12px; color: #94a3b8; }
.toolbar-row .spacer { flex: 1; }
.toolbar-row .status { font-size: 11px; color: #94a3b8; }
.toolbar-row .sep { width: 1px; height: 18px; background: #444; }
.toolbar-row .lbl { font-size: 11px; color: #94a3b8; font-weight: bold; }
.align-btn { width: 24px; height: 22px; padding: 0 !important; display: inline-flex; align-items: center; justify-content: center; font-size: 12px !important; }
.align-btn.active { background: #2563eb !important; }

#canvas-wrap { flex: 1; display: flex; align-items: center; justify-content: center; overflow: auto; padding: 20px; }
#page-container { position: relative; background: #000; box-shadow: 0 4px 24px rgba(0,0,0,0.5); }
#page-container svg { display: block; }

/* Editor-only styles applied via CSS (not in the SVG itself) */
.text-box { cursor: move; }
.text-box rect.bg { fill: rgba(0,0,0,0.35); stroke: rgba(255,255,255,0.4); stroke-width: 1; rx: 4; }
.text-box.selected rect.bg { stroke: #3b82f6; stroke-width: 2; stroke-dasharray: 6 3; }
.resize-handle { fill: #3b82f6; stroke: white; stroke-width: 1; cursor: nwse-resize; opacity: 0; }
.text-box.selected .resize-handle { opacity: 1; }

/* Modal */
#edit-modal { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.6); z-index: 100; align-items: center; justify-content: center; }
#edit-modal.open { display: flex; }
#edit-modal .modal-box { background: #1e293b; border-radius: 8px; padding: 20px; width: 520px; max-width: 90vw; }
#edit-modal h3 { margin-bottom: 12px; font-size: 16px; }
#edit-modal textarea { width: 100%; height: 120px; background: #0f172a; color: #eee; border: 1px solid #444; border-radius: 4px; padding: 8px; font-family: inherit; font-size: 14px; resize: vertical; }
#edit-modal .modal-btns { margin-top: 12px; display: flex; gap: 8px; justify-content: flex-end; }
#edit-modal .modal-btns button { padding: 6px 18px; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; font-size: 13px; }
#edit-modal .btn-cancel { background: #475569; color: #fff; }
#edit-modal .btn-save { background: #3b82f6; color: #fff; }
</style>
</head>
<body>
<div id="app">
  <div id="sidebar">
    <h2>Writing Prompts</h2>
    <div id="prompt-list"></div>
  </div>
  <div id="main">
    <!-- Row 1: Navigation + text sizes + alignment + edit -->
    <div class="toolbar-row">
      <button class="btn-nav" onclick="prevPage()">&larr;</button>
      <span class="page-info" id="page-info">1 / 10</span>
      <button class="btn-nav" onclick="nextPage()">&rarr;</button>
      <span class="sep"></span>
      <span class="lbl">Title:</span>
      <input type="number" id="title-size" value="34" min="10" max="72" onchange="onSizeChange()">
      <button class="align-btn" data-target="title" data-align="left" onclick="setAlign(this)" title="Left">&#9776;</button>
      <button class="align-btn" data-target="title" data-align="center" onclick="setAlign(this)" title="Center">&#9778;</button>
      <button class="align-btn" data-target="title" data-align="right" onclick="setAlign(this)" title="Right">&#9783;</button>
      <button class="btn-primary" onclick="editText('title')">Edit</button>
      <span class="sep"></span>
      <span class="lbl">Body:</span>
      <input type="number" id="body-size" value="21" min="10" max="48" onchange="onSizeChange()">
      <button class="align-btn" data-target="body" data-align="left" onclick="setAlign(this)" title="Left">&#9776;</button>
      <button class="align-btn" data-target="body" data-align="center" onclick="setAlign(this)" title="Center">&#9778;</button>
      <button class="align-btn" data-target="body" data-align="right" onclick="setAlign(this)" title="Right">&#9783;</button>
      <button class="btn-primary" onclick="editText('body')">Edit</button>
      <span class="sep"></span>
      <button class="btn-warn" onclick="resetCurrent()">Reset</button>
    </div>
    <!-- Row 2: Colors + shadow controls + generate -->
    <div class="toolbar-row">
      <span class="lbl">Title:</span>
      <label>color <input type="color" id="title-color" value="#ffffff" onchange="onStyleChange()"></label>
      <label>shadow <input type="color" id="title-shadow-color" value="#000000" onchange="onStyleChange()"></label>
      <label>dx<input type="number" id="title-shadow-dx" value="2" min="-10" max="10" onchange="onStyleChange()"></label>
      <label>dy<input type="number" id="title-shadow-dy" value="2" min="-10" max="10" onchange="onStyleChange()"></label>
      <label>blur<input type="number" id="title-shadow-blur" value="0" min="0" max="20" step="0.5" onchange="onStyleChange()"></label>
      <span class="sep"></span>
      <span class="lbl">Body:</span>
      <label>color <input type="color" id="body-color" value="#ffffff" onchange="onStyleChange()"></label>
      <label>shadow <input type="color" id="body-shadow-color" value="#000000" onchange="onStyleChange()"></label>
      <label>dx<input type="number" id="body-shadow-dx" value="2" min="-10" max="10" onchange="onStyleChange()"></label>
      <label>dy<input type="number" id="body-shadow-dy" value="2" min="-10" max="10" onchange="onStyleChange()"></label>
      <label>blur<input type="number" id="body-shadow-blur" value="0" min="0" max="20" step="0.5" onchange="onStyleChange()"></label>
      <span class="sep"></span>
      <label>Overlay: <input type="number" id="overlay-opacity" value="45" min="0" max="100" step="5" onchange="onStyleChange()">%</label>
      <span class="sep"></span>
      <label>Page:
        <select id="page-size" onchange="setPageSize(this.value)">
          <option value="letter" selected>Letter (8.5x11)</option>
          <option value="a4">A4</option>
          <option value="legal">Legal (8.5x14)</option>
          <option value="tabloid">Tabloid (11x17)</option>
          <option value="a5">A5</option>
        </select>
      </label>
      <span class="spacer"></span>
      <span class="status" id="status">Drag background to pan image</span>
      <button class="btn-success" id="generate-btn" onclick="generatePDF()">Generate PDF</button>
    </div>
    <div id="canvas-wrap">
      <div id="page-container"></div>
    </div>
  </div>
</div>

<div id="edit-modal">
  <div class="modal-box">
    <h3 id="edit-modal-title">Edit Title</h3>
    <textarea id="edit-modal-text"></textarea>
    <div class="modal-btns">
      <button class="btn-cancel" onclick="closeEditModal()">Cancel</button>
      <button class="btn-save" onclick="saveEditModal()">Save</button>
    </div>
  </div>
</div>

<script>
// ── State ──────────────────────────────────────────────────────────────
let prompts = [];
let currentIdx = 0;
let layouts = {};
let selectedBox = null;

// Page sizes in points (72pt = 1 inch)
const PAGE_SIZES = {
    "letter":  [612, 792],    // 8.5 x 11
    "legal":   [612, 1008],   // 8.5 x 14
    "tabloid": [792, 1224],   // 11 x 17
    "a4":      [595, 842],    // 210 x 297 mm
    "a5":      [420, 595],    // 148 x 210 mm
};
let pageSizeKey = "letter";
let PW = 612, PH = 792;
let displayScale = 1;

function setPageSize(key) {
    pageSizeKey = key;
    [PW, PH] = PAGE_SIZES[key];
    // Update all layouts with new page dims
    for (const id in layouts) {
        layouts[id].page_w = PW;
        layouts[id].page_h = PH;
    }
    renderPage();
}

function marginFrac() { return 72 / PW; }

function defaultLayout(prompt) {
    const mf = marginFrac();
    return {
        id: prompt.id,
        page_w: PW,
        page_h: PH,
        title_size: 34,
        body_size: 21,
        title_align: "left",
        body_align: "left",
        title_text: prompt.title,
        body_text: prompt.prompt,
        title_color: "#ffffff",
        body_color: "#ffffff",
        title_shadow_color: "#000000",
        title_shadow_dx: 2,
        title_shadow_dy: 2,
        title_shadow_blur: 0,
        body_shadow_color: "#000000",
        body_shadow_dx: 2,
        body_shadow_dy: 2,
        body_shadow_blur: 0,
        overlay_opacity: 0.45,
        img_offset_x: 0.5,
        img_offset_y: 0.5,
        img_w: prompt.img_w,
        img_h: prompt.img_h,
        title_box: { x: mf, y: 0.08, w: 1 - 2 * mf, h: 0.18 },
        body_box:  { x: mf, y: 0.30, w: 1 - 2 * mf, h: 0.55 },
    };
}

function imgGeometry(prompt) {
    if (!prompt.img_w) return { extraX: 0, extraY: 0, drawW: PW, drawH: PH };
    const scale = Math.max(PW / prompt.img_w, PH / prompt.img_h);
    const drawW = prompt.img_w * scale, drawH = prompt.img_h * scale;
    return { extraX: drawW - PW, extraY: drawH - PH, drawW, drawH };
}

// ── Init ──────────────────────────────────────────────────────────────
async function init() {
    const resp = await fetch("/api/prompts");
    prompts = await resp.json();
    prompts.forEach(p => { layouts[p.id] = defaultLayout(p); });
    renderSidebar();
    renderPage();
    window.addEventListener("resize", renderPage);
}

function renderSidebar() {
    const el = document.getElementById("prompt-list");
    el.innerHTML = prompts.map((p, i) => `
        <div class="prompt-item ${i === currentIdx ? 'active' : ''}" onclick="goToPage(${i})">
            <span class="dot ${p.has_image ? 'has-img' : 'no-img'}"></span>
            <span>${p.id}. ${p.title}</span>
        </div>
    `).join("");
}

function goToPage(idx) { currentIdx = idx; selectedBox = null; renderSidebar(); renderPage(); }
function prevPage() { if (currentIdx > 0) goToPage(currentIdx - 1); }
function nextPage() { if (currentIdx < prompts.length - 1) goToPage(currentIdx + 1); }

function resetCurrent() { layouts[prompts[currentIdx].id] = defaultLayout(prompts[currentIdx]); renderPage(); }

// ── Toolbar handlers ──────────────────────────────────────────────────
function onSizeChange() {
    const L = layouts[prompts[currentIdx].id];
    L.title_size = parseInt(document.getElementById("title-size").value) || 34;
    L.body_size = parseInt(document.getElementById("body-size").value) || 21;
    renderPage();
}

function setAlign(btn) {
    layouts[prompts[currentIdx].id][btn.dataset.target + "_align"] = btn.dataset.align;
    renderPage();
}

function onStyleChange() {
    const L = layouts[prompts[currentIdx].id];
    L.title_color = document.getElementById("title-color").value;
    L.body_color = document.getElementById("body-color").value;
    L.title_shadow_color = document.getElementById("title-shadow-color").value;
    L.title_shadow_dx = parseFloat(document.getElementById("title-shadow-dx").value) || 0;
    L.title_shadow_dy = parseFloat(document.getElementById("title-shadow-dy").value) || 0;
    L.title_shadow_blur = parseFloat(document.getElementById("title-shadow-blur").value) || 0;
    L.body_shadow_color = document.getElementById("body-shadow-color").value;
    L.body_shadow_dx = parseFloat(document.getElementById("body-shadow-dx").value) || 0;
    L.body_shadow_dy = parseFloat(document.getElementById("body-shadow-dy").value) || 0;
    L.body_shadow_blur = parseFloat(document.getElementById("body-shadow-blur").value) || 0;
    L.overlay_opacity = (parseInt(document.getElementById("overlay-opacity").value) || 45) / 100;
    renderPage();
}

function updateToolbar() {
    const L = layouts[prompts[currentIdx].id];
    document.getElementById("title-size").value = L.title_size;
    document.getElementById("body-size").value = L.body_size;
    document.getElementById("title-color").value = L.title_color;
    document.getElementById("body-color").value = L.body_color;
    document.getElementById("title-shadow-color").value = L.title_shadow_color;
    document.getElementById("title-shadow-dx").value = L.title_shadow_dx;
    document.getElementById("title-shadow-dy").value = L.title_shadow_dy;
    document.getElementById("title-shadow-blur").value = L.title_shadow_blur;
    document.getElementById("body-shadow-color").value = L.body_shadow_color;
    document.getElementById("body-shadow-dx").value = L.body_shadow_dx;
    document.getElementById("body-shadow-dy").value = L.body_shadow_dy;
    document.getElementById("body-shadow-blur").value = L.body_shadow_blur;
    document.getElementById("overlay-opacity").value = Math.round(L.overlay_opacity * 100);
    document.getElementById("page-size").value = pageSizeKey;
    document.querySelectorAll(".align-btn").forEach(btn => {
        btn.classList.toggle("active", L[btn.dataset.target + "_align"] === btn.dataset.align);
    });
}

// ── Text editing modal ────────────────────────────────────────────────
let editTarget = null;
function editText(target) {
    editTarget = target;
    const L = layouts[prompts[currentIdx].id];
    document.getElementById("edit-modal-title").textContent = target === "title" ? "Edit Title" : "Edit Body";
    document.getElementById("edit-modal-text").value = L[target + "_text"];
    document.getElementById("edit-modal").classList.add("open");
    setTimeout(() => document.getElementById("edit-modal-text").focus(), 50);
}
function closeEditModal() { document.getElementById("edit-modal").classList.remove("open"); editTarget = null; }
function saveEditModal() {
    if (!editTarget) return;
    layouts[prompts[currentIdx].id][editTarget + "_text"] = document.getElementById("edit-modal-text").value;
    closeEditModal(); renderPage();
}
document.getElementById("edit-modal-text").addEventListener("keydown", e => { if (e.key === "Escape") closeEditModal(); });

// ── SVG text rendering (shared logic with server) ─────────────────────
function wrapText(text, fontSize, maxWidth, isBold) {
    const charW = fontSize * (isBold ? 0.58 : 0.52);
    const words = text.split(/\s+/);
    const lines = [];
    let current = "";
    for (const word of words) {
        const test = current ? current + " " + word : word;
        if (test.length * charW > maxWidth && current) {
            lines.push(current);
            current = word;
        } else {
            current = test;
        }
    }
    if (current) lines.push(current);
    return lines;
}

function escHtml(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function buildTextSVG(text, box, fontSize, isBold, align, textColor, shadowColor, sdx, sdy, sblur, filterId) {
    const x = box.x * PW, yTop = box.y * PH, w = box.w * PW, h = box.h * PH;
    const pad = 8, lineH = fontSize * 1.25;
    const lines = wrapText(text, fontSize, w - 2 * pad, isBold);

    let anchor = "start", tx = x + pad;
    if (align === "center") { anchor = "middle"; tx = x + w / 2; }
    else if (align === "right") { anchor = "end"; tx = x + w - pad; }

    const weight = isBold ? "bold" : "normal";
    const hasShadow = sdx || sdy || sblur;
    let svg = "";

    // Shadow pass
    if (hasShadow) {
        if (sblur && filterId) svg += `<g filter="url(#${filterId})">\n`;
        let ty = yTop + fontSize + pad;
        for (const line of lines) {
            if (ty > yTop + h - 4) break;
            svg += `<text x="${tx + sdx}" y="${ty + sdy}" font-size="${fontSize}" font-weight="${weight}" font-family="Liberation Sans, Arial, sans-serif" text-anchor="${anchor}" fill="${shadowColor}">${escHtml(line)}</text>\n`;
            ty += lineH;
        }
        if (sblur && filterId) svg += `</g>\n`;
    }

    // Main text pass
    let ty = yTop + fontSize + pad;
    for (const line of lines) {
        if (ty > yTop + h - 4) break;
        svg += `<text x="${tx}" y="${ty}" font-size="${fontSize}" font-weight="${weight}" font-family="Liberation Sans, Arial, sans-serif" text-anchor="${anchor}" fill="${textColor}">${escHtml(line)}</text>\n`;
        ty += lineH;
    }
    return svg;
}

// ── Render page ───────────────────────────────────────────────────────
function renderPage() {
    const p = prompts[currentIdx];
    const L = layouts[p.id];
    document.getElementById("page-info").textContent = `${currentIdx + 1} / ${prompts.length}`;
    updateToolbar();

    const wrap = document.getElementById("canvas-wrap");
    displayScale = Math.min((wrap.clientWidth - 40) / PW, (wrap.clientHeight - 40) / PH);
    const svgW = PW * displayScale, svgH = PH * displayScale;

    const container = document.getElementById("page-container");
    container.style.width = svgW + "px";
    container.style.height = svgH + "px";

    let svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${svgW}" height="${svgH}" viewBox="0 0 ${PW} ${PH}">`;
    let defs = `<clipPath id="page-clip"><rect x="0" y="0" width="${PW}" height="${PH}"/></clipPath>`;
    if (L.title_shadow_blur > 0) defs += `<filter id="title-blur"><feGaussianBlur stdDeviation="${L.title_shadow_blur}"/></filter>`;
    if (L.body_shadow_blur > 0) defs += `<filter id="body-blur"><feGaussianBlur stdDeviation="${L.body_shadow_blur}"/></filter>`;
    svg += `<defs>${defs}</defs>`;

    if (p.has_image) {
        const geo = imgGeometry(p);
        const imgX = -geo.extraX * L.img_offset_x;
        const imgY = -geo.extraY * L.img_offset_y;
        svg += `<g clip-path="url(#page-clip)">`;
        svg += `<image href="/api/image/${p.id}" x="${imgX}" y="${imgY}" width="${geo.drawW}" height="${geo.drawH}" preserveAspectRatio="none"/>`;
        svg += `</g>`;
        svg += `<rect x="0" y="0" width="${PW}" height="${PH}" fill="rgba(0,0,0,${L.overlay_opacity})"/>`;
        svg += `<rect class="bg-drag" x="0" y="0" width="${PW}" height="${PH}" fill="transparent" style="cursor:grab;"/>`;
    } else {
        svg += `<rect x="0" y="0" width="${PW}" height="${PH}" fill="#222"/>`;
        svg += `<text text-anchor="middle" fill="#f87171" font-size="18" x="${PW/2}" y="${PH/2}">No image for prompt ${p.id}</text>`;
    }

    // Margin guides
    svg += `<rect x="72" y="72" width="${PW-144}" height="${PH-144}" fill="none" stroke="rgba(255,255,255,0.15)" stroke-width="0.5" stroke-dasharray="4 4"/>`;

    // Title text box (editor wrapper with drag/resize handles)
    svg += buildEditorBox("title", L.title_box,
        buildTextSVG(L.title_text, L.title_box, L.title_size, true, L.title_align, L.title_color,
            L.title_shadow_color, L.title_shadow_dx, L.title_shadow_dy, L.title_shadow_blur, "title-blur"));

    // Body text box
    svg += buildEditorBox("body", L.body_box,
        buildTextSVG(L.body_text, L.body_box, L.body_size, false, L.body_align, L.body_color,
            L.body_shadow_color, L.body_shadow_dx, L.body_shadow_dy, L.body_shadow_blur, "body-blur"));

    svg += `</svg>`;
    container.innerHTML = svg;
    attachDragListeners();
}

function buildEditorBox(id, box, innerSVG) {
    const x = box.x * PW, y = box.y * PH, w = box.w * PW, h = box.h * PH;
    const sel = selectedBox === id;
    let g = `<g class="text-box ${sel ? 'selected' : ''}" data-box="${id}">`;
    g += `<rect class="bg" x="${x}" y="${y}" width="${w}" height="${h}"/>`;
    g += innerSVG;
    g += `<rect class="resize-handle" data-box="${id}" x="${x+w-12}" y="${y+h-12}" width="12" height="12"/>`;
    g += `</g>`;
    return g;
}

// ── Drag, Resize & Image Pan ──────────────────────────────────────────
let dragState = null;

function attachDragListeners() {
    const svg = document.querySelector("#page-container svg");
    if (!svg) return;
    svg.addEventListener("mousedown", onMouseDown);
    svg.addEventListener("mousemove", onMouseMove);
    svg.addEventListener("mouseup", onMouseUp);
    svg.addEventListener("mouseleave", onMouseUp);
}

function getSVGPoint(e) {
    const svg = document.querySelector("#page-container svg");
    const r = svg.getBoundingClientRect();
    return { x: (e.clientX - r.left) / displayScale, y: (e.clientY - r.top) / displayScale };
}

function onMouseDown(e) {
    const handle = e.target.closest(".resize-handle");
    const box = e.target.closest(".text-box");
    const bgDrag = e.target.closest(".bg-drag");
    const pt = getSVGPoint(e);
    const L = layouts[prompts[currentIdx].id];

    if (box) {
        const bid = box.getAttribute("data-box");
        if (!bid) return;
        selectedBox = bid;
        const bx = L[bid + "_box"];
        dragState = handle
            ? { mode: "resize", boxId: bid, startX: pt.x, startY: pt.y, origW: bx.w, origH: bx.h }
            : { mode: "move", boxId: bid, startX: pt.x, startY: pt.y, origX: bx.x, origY: bx.y };
        e.preventDefault(); renderPage();
    } else if (bgDrag) {
        selectedBox = null;
        dragState = { mode: "pan", startX: pt.x, startY: pt.y, origOX: L.img_offset_x, origOY: L.img_offset_y };
        e.preventDefault(); renderPage();
    }
}

function onMouseMove(e) {
    if (!dragState) return;
    const pt = getSVGPoint(e);
    const p = prompts[currentIdx];
    const L = layouts[p.id];

    if (dragState.mode === "pan") {
        const geo = imgGeometry(p);
        if (geo.extraX > 0) L.img_offset_x = clamp(dragState.origOX - (pt.x - dragState.startX) / geo.extraX, 0, 1);
        if (geo.extraY > 0) L.img_offset_y = clamp(dragState.origOY - (pt.y - dragState.startY) / geo.extraY, 0, 1);
    } else {
        const dx = (pt.x - dragState.startX) / PW, dy = (pt.y - dragState.startY) / PH;
        const bx = L[dragState.boxId + "_box"];
        if (dragState.mode === "move") {
            bx.x = clamp(dragState.origX + dx, 0, 1 - bx.w);
            bx.y = clamp(dragState.origY + dy, 0, 1 - bx.h);
        } else {
            bx.w = clamp(dragState.origW + dx, 0.1, 1 - bx.x);
            bx.h = clamp(dragState.origH + dy, 0.05, 1 - bx.y);
        }
    }
    renderPage(); e.preventDefault();
}

function onMouseUp() { dragState = null; }
function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

// ── PDF generation ────────────────────────────────────────────────────
async function generatePDF() {
    const btn = document.getElementById("generate-btn");
    const status = document.getElementById("status");
    btn.disabled = true; btn.textContent = "Generating..."; status.textContent = "";

    const toGenerate = prompts.filter(p => p.has_image).map(p => layouts[p.id]);
    try {
        const resp = await fetch("/api/generate-pdf", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ layouts: toGenerate }),
        });
        if (!resp.ok) throw new Error(`Server error: ${resp.status}`);
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a"); a.href = url; a.download = "writing_prompts.pdf"; a.click();
        URL.revokeObjectURL(url);
        status.textContent = "PDF downloaded!";
    } catch (err) { status.textContent = "Error: " + err.message; }
    finally { btn.disabled = false; btn.textContent = "Generate PDF"; }
}

// ── Keyboard shortcuts ────────────────────────────────────────────────
document.addEventListener("keydown", e => {
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
    if (e.key === "ArrowLeft") prevPage();
    if (e.key === "ArrowRight") nextPage();
    if (e.key === "Escape") { selectedBox = null; renderPage(); }
    if (e.key === "Tab") { e.preventDefault(); selectedBox = selectedBox === "title" ? "body" : "title"; renderPage(); }
});

init();
</script>
</body>
</html>
"""

# ── Main ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Writing prompt layout editor")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--prompts", default="writing-prompts.json")
    parser.add_argument("--images", default="writing-prompts")
    args = parser.parse_args()
    PROMPTS_FILE = args.prompts
    IMAGE_DIR = args.images
    print(f"Starting layout editor on http://localhost:{args.port}")
    print(f"  Prompts: {PROMPTS_FILE}")
    print(f"  Images:  {IMAGE_DIR}/")
    app.run(host="0.0.0.0", port=args.port, debug=True)
