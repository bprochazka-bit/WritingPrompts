#!/usr/bin/env python3
"""Web UI for laying out writing prompt text over background images.

Drag and resize title/body text boxes on each page, then generate a PDF.

Usage:
    python3 layout_editor.py [--port 5000] [--prompts writing-prompts.json]
"""

import argparse
import json
import os
import glob
from io import BytesIO

from flask import Flask, request, jsonify, send_file, send_from_directory, Response
from PIL import Image
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from reportlab.lib.colors import Color
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

app = Flask(__name__)

# ── Constants ──────────────────────────────────────────────────────────────
WIDTH, HEIGHT = letter  # 612 x 792 pt
MARGIN = 1.0 * inch
PROMPTS_FILE = "writing-prompts.json"
IMAGE_DIR = "writing-prompts"

# ── Font registration ─────────────────────────────────────────────────────
FONT_DIR = "/usr/share/fonts/truetype/liberation"
_fonts_registered = False

def _register_fonts():
    global _fonts_registered
    if _fonts_registered:
        return
    pdfmetrics.registerFont(TTFont("LibSans", f"{FONT_DIR}/LiberationSans-Regular.ttf"))
    pdfmetrics.registerFont(TTFont("LibSansBold", f"{FONT_DIR}/LiberationSans-Bold.ttf"))
    _fonts_registered = True


# ── Helpers ────────────────────────────────────────────────────────────────
def _find_image(prompt_id):
    pattern = os.path.join(IMAGE_DIR, f"{prompt_id}-*")
    matches = glob.glob(pattern)
    return matches[0] if matches else None


def _load_prompts():
    with open(PROMPTS_FILE) as f:
        return json.load(f)


def wrap_text(c, text, font, size, max_width):
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


def draw_text_with_shadow(c, text, x, y, font, size, shadow_offset=2):
    c.setFont(font, size)
    c.setFillColor(Color(0, 0, 0, 0.6))
    c.drawString(x + shadow_offset, y - shadow_offset, text)
    c.setFillColor(Color(1, 1, 1, 1))
    c.drawString(x, y, text)


# ── Routes ─────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return HTML_PAGE


@app.route("/api/prompts")
def api_prompts():
    data = _load_prompts()
    prompts = data["prompts"]
    result = []
    for p in prompts:
        img_path = _find_image(p["id"])
        result.append({
            "id": p["id"],
            "title": p["title"],
            "prompt": p["prompt"],
            "theme": p["theme"],
            "has_image": img_path is not None,
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
    """Generate PDF from layout data.

    Expects JSON: { layouts: [ { id, title_box: {x,y,w,h}, body_box: {x,y,w,h} }, ... ] }
    All coordinates are fractions of page size (0-1).
    """
    _register_fonts()
    data = request.get_json()
    layouts = data.get("layouts", [])
    prompts_data = {p["id"]: p for p in _load_prompts()["prompts"]}

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)

    for layout in layouts:
        pid = layout["id"]
        prompt = prompts_data.get(pid)
        if not prompt:
            continue

        img_path = _find_image(pid)
        if not img_path:
            continue

        pw, ph = WIDTH, HEIGHT
        c.setPageSize((pw, ph))

        # Draw background image (cover full page)
        img = Image.open(img_path)
        iw, ih = img.size
        scale = max(pw / iw, ph / ih)
        draw_w, draw_h = iw * scale, ih * scale
        x_off = (pw - draw_w) / 2
        y_off = (ph - draw_h) / 2
        c.drawImage(ImageReader(img), x_off, y_off, draw_w, draw_h)

        # Dark overlay
        c.setFillColor(Color(0, 0, 0, 0.45))
        c.rect(0, 0, pw, ph, fill=1, stroke=0)

        # Title box (coordinates are fractions 0-1 from top-left)
        tb = layout["title_box"]
        tx = tb["x"] * pw
        tw = tb["w"] * pw
        # Convert from top-left origin (web) to bottom-left origin (PDF)
        t_top_pdf = ph - tb["y"] * ph
        title_font = "LibSansBold"
        title_size = layout.get("title_size", 34)
        y = t_top_pdf
        for line in wrap_text(c, prompt["title"], title_font, title_size, tw):
            draw_text_with_shadow(c, line, tx, y, title_font, title_size, shadow_offset=3)
            y -= title_size + 8

        # Body box
        bb = layout["body_box"]
        bx = bb["x"] * pw
        bw = bb["w"] * pw
        b_top_pdf = ph - bb["y"] * ph
        body_font = "LibSans"
        body_size = layout.get("body_size", 21)
        y = b_top_pdf
        for line in wrap_text(c, prompt["prompt"], body_font, body_size, bw):
            draw_text_with_shadow(c, line, bx, y, body_font, body_size, shadow_offset=2)
            y -= body_size + 7

        c.showPage()

    c.save()
    buf.seek(0)
    return send_file(buf, mimetype="application/pdf", download_name="writing_prompts.pdf")


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
#toolbar { padding: 8px 16px; background: #16213e; border-bottom: 1px solid #333; display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
#toolbar button { padding: 6px 16px; border: none; border-radius: 4px; cursor: pointer; font-size: 13px; font-weight: bold; }
#toolbar .btn-primary { background: #3b82f6; color: #fff; }
#toolbar .btn-primary:hover { background: #2563eb; }
#toolbar .btn-success { background: #22c55e; color: #fff; }
#toolbar .btn-success:hover { background: #16a34a; }
#toolbar .btn-nav { background: #475569; color: #fff; }
#toolbar .btn-nav:hover { background: #64748b; }
#toolbar label { font-size: 13px; display: flex; align-items: center; gap: 4px; }
#toolbar input[type=number] { width: 50px; padding: 2px 4px; background: #1e293b; color: #eee; border: 1px solid #444; border-radius: 3px; }
#toolbar .page-info { font-size: 13px; color: #94a3b8; }
#toolbar .spacer { flex: 1; }
#toolbar .status { font-size: 12px; color: #94a3b8; }

#canvas-wrap { flex: 1; display: flex; align-items: center; justify-content: center; overflow: auto; padding: 20px; }

/* The SVG container (letter aspect ratio) */
#page-container { position: relative; background: #000; box-shadow: 0 4px 24px rgba(0,0,0,0.5); }
#page-container svg { display: block; }

/* Draggable text boxes */
.text-box { cursor: move; }
.text-box rect.bg { fill: rgba(0,0,0,0.35); stroke: rgba(255,255,255,0.4); stroke-width: 1; rx: 4; }
.text-box.selected rect.bg { stroke: #3b82f6; stroke-width: 2; stroke-dasharray: 6 3; }
.text-box text { fill: white; font-family: 'Liberation Sans', Arial, sans-serif; }
.text-box .title-text { font-weight: bold; }

/* Resize handles */
.resize-handle { fill: #3b82f6; stroke: white; stroke-width: 1; cursor: nwse-resize; opacity: 0; }
.text-box.selected .resize-handle { opacity: 1; }

/* Reset button on each page */
.no-image-msg { text-anchor: middle; fill: #f87171; font-size: 18px; }
</style>
</head>
<body>
<div id="app">
  <div id="sidebar">
    <h2>Writing Prompts</h2>
    <div id="prompt-list"></div>
  </div>
  <div id="main">
    <div id="toolbar">
      <button class="btn-nav" onclick="prevPage()">&larr; Prev</button>
      <span class="page-info" id="page-info">1 / 10</span>
      <button class="btn-nav" onclick="nextPage()">Next &rarr;</button>
      <label>Title size: <input type="number" id="title-size" value="34" min="10" max="72" onchange="onSizeChange()"></label>
      <label>Body size: <input type="number" id="body-size" value="21" min="10" max="48" onchange="onSizeChange()"></label>
      <button class="btn-primary" onclick="resetCurrent()">Reset Layout</button>
      <span class="spacer"></span>
      <span class="status" id="status"></span>
      <button class="btn-success" id="generate-btn" onclick="generatePDF()">Generate PDF</button>
    </div>
    <div id="canvas-wrap">
      <div id="page-container"></div>
    </div>
  </div>
</div>

<script>
// ── State ──────────────────────────────────────────────────────────────
let prompts = [];
let currentIdx = 0;
let layouts = {};  // keyed by prompt id
let selectedBox = null;  // "title" or "body"

// Page constants (points, matches PDF)
const PW = 612, PH = 792;
const MARGIN_FRAC = 72 / 612;  // 1" = 72pt on 612pt page

// Display scale — fit the SVG into the viewport
let displayScale = 1;

// ── Default layout ────────────────────────────────────────────────────
function defaultLayout(prompt) {
    return {
        id: prompt.id,
        title_size: 34,
        body_size: 21,
        title_box: { x: MARGIN_FRAC, y: 0.08, w: 1 - 2 * MARGIN_FRAC, h: 0.18 },
        body_box:  { x: MARGIN_FRAC, y: 0.30, w: 1 - 2 * MARGIN_FRAC, h: 0.55 },
    };
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

function goToPage(idx) {
    currentIdx = idx;
    selectedBox = null;
    renderSidebar();
    renderPage();
}

function prevPage() { if (currentIdx > 0) goToPage(currentIdx - 1); }
function nextPage() { if (currentIdx < prompts.length - 1) goToPage(currentIdx + 1); }

function resetCurrent() {
    const p = prompts[currentIdx];
    layouts[p.id] = defaultLayout(p);
    renderPage();
}

function onSizeChange() {
    const p = prompts[currentIdx];
    const L = layouts[p.id];
    L.title_size = parseInt(document.getElementById("title-size").value) || 34;
    L.body_size = parseInt(document.getElementById("body-size").value) || 21;
    renderPage();
}

// ── Render ────────────────────────────────────────────────────────────
function renderPage() {
    const p = prompts[currentIdx];
    const L = layouts[p.id];
    document.getElementById("page-info").textContent = `${currentIdx + 1} / ${prompts.length}`;
    document.getElementById("title-size").value = L.title_size;
    document.getElementById("body-size").value = L.body_size;

    // Compute display size to fit viewport
    const wrap = document.getElementById("canvas-wrap");
    const maxW = wrap.clientWidth - 40;
    const maxH = wrap.clientHeight - 40;
    displayScale = Math.min(maxW / PW, maxH / PH);
    const svgW = PW * displayScale;
    const svgH = PH * displayScale;

    const container = document.getElementById("page-container");
    container.style.width = svgW + "px";
    container.style.height = svgH + "px";

    // Build SVG
    let svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${svgW}" height="${svgH}" viewBox="0 0 ${PW} ${PH}">`;

    // Background image
    if (p.has_image) {
        svg += `<image href="/api/image/${p.id}" x="0" y="0" width="${PW}" height="${PH}" preserveAspectRatio="xMidYMid slice"/>`;
        // Dark overlay
        svg += `<rect x="0" y="0" width="${PW}" height="${PH}" fill="rgba(0,0,0,0.45)"/>`;
    } else {
        svg += `<rect x="0" y="0" width="${PW}" height="${PH}" fill="#222"/>`;
        svg += `<text class="no-image-msg" x="${PW/2}" y="${PH/2}">No image for prompt ${p.id}</text>`;
    }

    // 1" margin guides (subtle dashed lines)
    const m = 72;
    svg += `<rect x="${m}" y="${m}" width="${PW - 2*m}" height="${PH - 2*m}" fill="none" stroke="rgba(255,255,255,0.15)" stroke-width="0.5" stroke-dasharray="4 4"/>`;

    // Title box
    svg += renderTextBox("title", L.title_box, p.title, L.title_size, true);
    // Body box
    svg += renderTextBox("body", L.body_box, p.prompt, L.body_size, false);

    svg += `</svg>`;
    container.innerHTML = svg;

    // Attach event listeners
    attachDragListeners();
}

function renderTextBox(id, box, text, fontSize, isBold) {
    const x = box.x * PW, y = box.y * PH, w = box.w * PW, h = box.h * PH;
    const isSelected = selectedBox === id;
    let g = `<g class="text-box ${isSelected ? 'selected' : ''}" data-box="${id}">`;
    g += `<rect class="bg" x="${x}" y="${y}" width="${w}" height="${h}"/>`;

    // Wrap text into lines
    const lines = wrapTextSVG(text, fontSize, w - 16, isBold);
    const lineHeight = fontSize * 1.25;
    let ty = y + fontSize + 8;
    for (const line of lines) {
        if (ty > y + h - 4) break;  // clip to box visually
        g += `<text x="${x + 8}" y="${ty}" font-size="${fontSize}" class="${isBold ? 'title-text' : ''}">${escHtml(line)}</text>`;
        ty += lineHeight;
    }

    // Resize handle (bottom-right corner)
    const hs = 12;
    g += `<rect class="resize-handle" data-box="${id}" x="${x + w - hs}" y="${y + h - hs}" width="${hs}" height="${hs}"/>`;

    g += `</g>`;
    return g;
}

function wrapTextSVG(text, fontSize, maxWidth, isBold) {
    // Approximate character width (Liberation Sans is ~0.55em for regular, ~0.58 for bold)
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

// ── Drag & Resize ─────────────────────────────────────────────────────
function attachDragListeners() {
    const svg = document.querySelector("#page-container svg");
    if (!svg) return;

    // Click to select
    svg.addEventListener("mousedown", onMouseDown);
    svg.addEventListener("mousemove", onMouseMove);
    svg.addEventListener("mouseup", onMouseUp);
    // Deselect on background click
    svg.addEventListener("click", (e) => {
        if (!e.target.closest(".text-box")) {
            selectedBox = null;
            renderPage();
        }
    });
}

let dragState = null;

function getSVGPoint(e) {
    const svg = document.querySelector("#page-container svg");
    const rect = svg.getBoundingClientRect();
    return {
        x: (e.clientX - rect.left) / displayScale,
        y: (e.clientY - rect.top) / displayScale
    };
}

function onMouseDown(e) {
    const handle = e.target.closest(".resize-handle");
    const box = e.target.closest(".text-box");
    if (!box) return;

    const boxId = (handle || box.querySelector("[data-box]")).getAttribute("data-box") ||
                  box.getAttribute("data-box") || box.querySelector(".resize-handle")?.getAttribute("data-box");

    // Get box id from the group's data attribute
    const bid = box.getAttribute("data-box");
    if (!bid) return;

    selectedBox = bid;

    const pt = getSVGPoint(e);
    const p = prompts[currentIdx];
    const L = layouts[p.id];
    const bx = L[bid + "_box"];

    if (handle) {
        // Resize mode
        dragState = {
            mode: "resize",
            boxId: bid,
            startX: pt.x,
            startY: pt.y,
            origW: bx.w,
            origH: bx.h,
        };
    } else {
        // Move mode
        dragState = {
            mode: "move",
            boxId: bid,
            startX: pt.x,
            startY: pt.y,
            origX: bx.x,
            origY: bx.y,
        };
    }

    e.preventDefault();
    renderPage();
}

function onMouseMove(e) {
    if (!dragState) return;
    const pt = getSVGPoint(e);
    const dx = (pt.x - dragState.startX) / PW;
    const dy = (pt.y - dragState.startY) / PH;

    const p = prompts[currentIdx];
    const L = layouts[p.id];
    const bx = L[dragState.boxId + "_box"];

    if (dragState.mode === "move") {
        bx.x = clamp(dragState.origX + dx, 0, 1 - bx.w);
        bx.y = clamp(dragState.origY + dy, 0, 1 - bx.h);
    } else {
        bx.w = clamp(dragState.origW + dx, 0.1, 1 - bx.x);
        bx.h = clamp(dragState.origH + dy, 0.05, 1 - bx.y);
    }

    renderPage();
    e.preventDefault();
}

function onMouseUp() {
    dragState = null;
}

function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

// ── PDF generation ────────────────────────────────────────────────────
async function generatePDF() {
    const btn = document.getElementById("generate-btn");
    const status = document.getElementById("status");
    btn.disabled = true;
    btn.textContent = "Generating...";
    status.textContent = "";

    // Only include prompts that have images
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
        const a = document.createElement("a");
        a.href = url;
        a.download = "writing_prompts.pdf";
        a.click();
        URL.revokeObjectURL(url);
        status.textContent = "PDF downloaded!";
    } catch (err) {
        status.textContent = "Error: " + err.message;
    } finally {
        btn.disabled = false;
        btn.textContent = "Generate PDF";
    }
}

// ── Keyboard shortcuts ────────────────────────────────────────────────
document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT") return;
    if (e.key === "ArrowLeft") prevPage();
    if (e.key === "ArrowRight") nextPage();
    if (e.key === "Tab") {
        e.preventDefault();
        selectedBox = selectedBox === "title" ? "body" : "title";
        renderPage();
    }
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
