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


def draw_text_with_shadow(c, text, x, y, font, size, shadow_offset=2, align="left", box_w=None):
    c.setFont(font, size)
    if align == "center" and box_w:
        tw = c.stringWidth(text, font, size)
        x = x + (box_w - tw) / 2
    elif align == "right" and box_w:
        tw = c.stringWidth(text, font, size)
        x = x + box_w - tw
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
        img_w, img_h = 0, 0
        if img_path:
            img = Image.open(img_path)
            img_w, img_h = img.size
        result.append({
            "id": p["id"],
            "title": p["title"],
            "prompt": p["prompt"],
            "theme": p["theme"],
            "has_image": img_path is not None,
            "img_w": img_w,
            "img_h": img_h,
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

        # Draw background image with offset
        img = Image.open(img_path)
        iw, ih = img.size
        scale = max(pw / iw, ph / ih)
        draw_w, draw_h = iw * scale, ih * scale
        # img_offset: 0.5 = centered (default)
        img_ox = layout.get("img_offset_x", 0.5)
        img_oy = layout.get("img_offset_y", 0.5)
        extra_x = draw_w - pw
        extra_y = draw_h - ph
        x_off = -extra_x * img_ox
        y_off = -extra_y * (1 - img_oy)  # PDF Y is flipped
        c.saveState()
        clip = c.beginPath()
        clip.rect(0, 0, pw, ph)
        c.clipPath(clip, stroke=0)
        c.drawImage(ImageReader(img), x_off, y_off, draw_w, draw_h)
        c.restoreState()

        # Dark overlay
        c.setFillColor(Color(0, 0, 0, 0.45))
        c.rect(0, 0, pw, ph, fill=1, stroke=0)

        # Title box (coordinates are fractions 0-1 from top-left)
        # SVG places first baseline at: box_top + fontSize + pad (8px)
        # PDF Y is flipped: pdf_y = ph - svg_y
        tb = layout["title_box"]
        tx = tb["x"] * pw
        tw = tb["w"] * pw
        title_font = "LibSansBold"
        title_size = layout.get("title_size", 34)
        title_align = layout.get("title_align", "left")
        title_text = layout.get("title_text", prompt["title"])
        line_h = title_size + 8
        # First baseline in SVG coords: tb["y"]*ph + title_size + 8
        y = ph - (tb["y"] * ph + title_size + 8)
        for line in wrap_text(c, title_text, title_font, title_size, tw):
            draw_text_with_shadow(c, line, tx, y, title_font, title_size,
                                  shadow_offset=3, align=title_align, box_w=tw)
            y -= title_size * 1.25

        # Body box
        bb = layout["body_box"]
        bx = bb["x"] * pw
        bw = bb["w"] * pw
        body_font = "LibSans"
        body_size = layout.get("body_size", 21)
        body_align = layout.get("body_align", "left")
        body_text = layout.get("body_text", prompt["prompt"])
        y = ph - (bb["y"] * ph + body_size + 8)
        for line in wrap_text(c, body_text, body_font, body_size, bw):
            draw_text_with_shadow(c, line, bx, y, body_font, body_size,
                                  shadow_offset=2, align=body_align, box_w=bw)
            y -= body_size * 1.25

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

/* Toolbar rows */
.toolbar-row { padding: 6px 16px; background: #16213e; border-bottom: 1px solid #333; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.toolbar-row button { padding: 5px 14px; border: none; border-radius: 4px; cursor: pointer; font-size: 12px; font-weight: bold; }
.toolbar-row .btn-primary { background: #3b82f6; color: #fff; }
.toolbar-row .btn-primary:hover { background: #2563eb; }
.toolbar-row .btn-success { background: #22c55e; color: #fff; }
.toolbar-row .btn-success:hover { background: #16a34a; }
.toolbar-row .btn-nav { background: #475569; color: #fff; }
.toolbar-row .btn-nav:hover { background: #64748b; }
.toolbar-row .btn-warn { background: #f59e0b; color: #000; }
.toolbar-row .btn-warn:hover { background: #d97706; }
.toolbar-row label { font-size: 12px; display: flex; align-items: center; gap: 4px; }
.toolbar-row input[type=number] { width: 48px; padding: 2px 4px; background: #1e293b; color: #eee; border: 1px solid #444; border-radius: 3px; font-size: 12px; }
.toolbar-row select { padding: 2px 4px; background: #1e293b; color: #eee; border: 1px solid #444; border-radius: 3px; font-size: 12px; }
.toolbar-row .page-info { font-size: 12px; color: #94a3b8; }
.toolbar-row .spacer { flex: 1; }
.toolbar-row .status { font-size: 12px; color: #94a3b8; }
.toolbar-row .sep { width: 1px; height: 20px; background: #444; }
.align-btn { width: 28px; height: 26px; padding: 0 !important; display: inline-flex; align-items: center; justify-content: center; font-size: 14px !important; }
.align-btn.active { background: #2563eb !important; }

#canvas-wrap { flex: 1; display: flex; align-items: center; justify-content: center; overflow: auto; padding: 20px; }

/* The SVG container */
#page-container { position: relative; background: #000; box-shadow: 0 4px 24px rgba(0,0,0,0.5); }
#page-container svg { display: block; }

/* Draggable text boxes */
.text-box { cursor: move; }
.text-box rect.bg { fill: rgba(0,0,0,0.35); stroke: rgba(255,255,255,0.4); stroke-width: 1; rx: 4; }
.text-box.selected rect.bg { stroke: #3b82f6; stroke-width: 2; stroke-dasharray: 6 3; }
.text-box text { fill: white; font-family: 'Liberation Sans', Arial, sans-serif; }
.text-box .title-text { font-weight: bold; }
.resize-handle { fill: #3b82f6; stroke: white; stroke-width: 1; cursor: nwse-resize; opacity: 0; }
.text-box.selected .resize-handle { opacity: 1; }
.no-image-msg { text-anchor: middle; fill: #f87171; font-size: 18px; }

/* Text edit modal */
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
    <div class="toolbar-row">
      <button class="btn-nav" onclick="prevPage()">&larr;</button>
      <span class="page-info" id="page-info">1 / 10</span>
      <button class="btn-nav" onclick="nextPage()">&rarr;</button>
      <span class="sep"></span>
      <label>Title: <input type="number" id="title-size" value="34" min="10" max="72" onchange="onSizeChange()"></label>
      <button class="align-btn" data-target="title" data-align="left" onclick="setAlign(this)" title="Left">&#9776;</button>
      <button class="align-btn" data-target="title" data-align="center" onclick="setAlign(this)" title="Center">&#9778;</button>
      <button class="align-btn" data-target="title" data-align="right" onclick="setAlign(this)" title="Right">&#9783;</button>
      <button class="btn-primary" style="font-size:11px" onclick="editText('title')">Edit Title</button>
      <span class="sep"></span>
      <label>Body: <input type="number" id="body-size" value="21" min="10" max="48" onchange="onSizeChange()"></label>
      <button class="align-btn" data-target="body" data-align="left" onclick="setAlign(this)" title="Left">&#9776;</button>
      <button class="align-btn" data-target="body" data-align="center" onclick="setAlign(this)" title="Center">&#9778;</button>
      <button class="align-btn" data-target="body" data-align="right" onclick="setAlign(this)" title="Right">&#9783;</button>
      <button class="btn-primary" style="font-size:11px" onclick="editText('body')">Edit Body</button>
      <span class="sep"></span>
      <button class="btn-warn" onclick="resetCurrent()">Reset</button>
      <span class="spacer"></span>
      <span class="status" id="status">Drag background to pan image</span>
      <button class="btn-success" id="generate-btn" onclick="generatePDF()">Generate PDF</button>
    </div>
    <div id="canvas-wrap">
      <div id="page-container"></div>
    </div>
  </div>
</div>

<!-- Text edit modal -->
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

const PW = 612, PH = 792;
const MARGIN_FRAC = 72 / 612;
let displayScale = 1;

// ── Default layout ────────────────────────────────────────────────────
function defaultLayout(prompt) {
    return {
        id: prompt.id,
        title_size: 34,
        body_size: 21,
        title_align: "left",
        body_align: "left",
        title_text: prompt.title,
        body_text: prompt.prompt,
        img_offset_x: 0.5,
        img_offset_y: 0.5,
        title_box: { x: MARGIN_FRAC, y: 0.08, w: 1 - 2 * MARGIN_FRAC, h: 0.18 },
        body_box:  { x: MARGIN_FRAC, y: 0.30, w: 1 - 2 * MARGIN_FRAC, h: 0.55 },
    };
}

// ── Compute image geometry (how much pan room) ────────────────────────
function imgGeometry(prompt) {
    if (!prompt.img_w) return { extraX: 0, extraY: 0, drawW: PW, drawH: PH };
    const iw = prompt.img_w, ih = prompt.img_h;
    const scale = Math.max(PW / iw, PH / ih);
    const drawW = iw * scale, drawH = ih * scale;
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

// ── Alignment ─────────────────────────────────────────────────────────
function setAlign(btn) {
    const target = btn.getAttribute("data-target");
    const align = btn.getAttribute("data-align");
    const p = prompts[currentIdx];
    layouts[p.id][target + "_align"] = align;
    renderPage();
}

function updateAlignButtons() {
    const p = prompts[currentIdx];
    const L = layouts[p.id];
    document.querySelectorAll(".align-btn").forEach(btn => {
        const target = btn.getAttribute("data-target");
        const align = btn.getAttribute("data-align");
        btn.classList.toggle("active", L[target + "_align"] === align);
    });
}

// ── Text editing modal ────────────────────────────────────────────────
let editTarget = null;

function editText(target) {
    editTarget = target;
    const p = prompts[currentIdx];
    const L = layouts[p.id];
    document.getElementById("edit-modal-title").textContent = target === "title" ? "Edit Title" : "Edit Body";
    document.getElementById("edit-modal-text").value = L[target + "_text"];
    document.getElementById("edit-modal").classList.add("open");
    setTimeout(() => document.getElementById("edit-modal-text").focus(), 50);
}

function closeEditModal() {
    document.getElementById("edit-modal").classList.remove("open");
    editTarget = null;
}

function saveEditModal() {
    if (!editTarget) return;
    const p = prompts[currentIdx];
    const L = layouts[p.id];
    L[editTarget + "_text"] = document.getElementById("edit-modal-text").value;
    closeEditModal();
    renderPage();
}

// Handle Escape and Enter in modal
document.getElementById("edit-modal-text").addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeEditModal();
});

// ── Render ────────────────────────────────────────────────────────────
function renderPage() {
    const p = prompts[currentIdx];
    const L = layouts[p.id];
    document.getElementById("page-info").textContent = `${currentIdx + 1} / ${prompts.length}`;
    document.getElementById("title-size").value = L.title_size;
    document.getElementById("body-size").value = L.body_size;
    updateAlignButtons();

    const wrap = document.getElementById("canvas-wrap");
    const maxW = wrap.clientWidth - 40;
    const maxH = wrap.clientHeight - 40;
    displayScale = Math.min(maxW / PW, maxH / PH);
    const svgW = PW * displayScale;
    const svgH = PH * displayScale;

    const container = document.getElementById("page-container");
    container.style.width = svgW + "px";
    container.style.height = svgH + "px";

    let svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${svgW}" height="${svgH}" viewBox="0 0 ${PW} ${PH}">`;
    svg += `<defs><clipPath id="page-clip"><rect x="0" y="0" width="${PW}" height="${PH}"/></clipPath></defs>`;

    if (p.has_image) {
        const geo = imgGeometry(p);
        const imgX = -geo.extraX * L.img_offset_x;
        const imgY = -geo.extraY * L.img_offset_y;
        svg += `<g clip-path="url(#page-clip)">`;
        svg += `<image href="/api/image/${p.id}" x="${imgX}" y="${imgY}" width="${geo.drawW}" height="${geo.drawH}" preserveAspectRatio="none"/>`;
        svg += `</g>`;
        // Dark overlay
        svg += `<rect x="0" y="0" width="${PW}" height="${PH}" fill="rgba(0,0,0,0.45)"/>`;
        // Invisible rect for background drag (behind text boxes)
        svg += `<rect class="bg-drag" x="0" y="0" width="${PW}" height="${PH}" fill="transparent" style="cursor: grab;"/>`;
    } else {
        svg += `<rect x="0" y="0" width="${PW}" height="${PH}" fill="#222"/>`;
        svg += `<text class="no-image-msg" x="${PW/2}" y="${PH/2}">No image for prompt ${p.id}</text>`;
    }

    // 1" margin guides
    const m = 72;
    svg += `<rect x="${m}" y="${m}" width="${PW - 2*m}" height="${PH - 2*m}" fill="none" stroke="rgba(255,255,255,0.15)" stroke-width="0.5" stroke-dasharray="4 4"/>`;

    svg += renderTextBox("title", L.title_box, L.title_text, L.title_size, true, L.title_align);
    svg += renderTextBox("body", L.body_box, L.body_text, L.body_size, false, L.body_align);

    svg += `</svg>`;
    container.innerHTML = svg;
    attachDragListeners();
}

function renderTextBox(id, box, text, fontSize, isBold, align) {
    const x = box.x * PW, y = box.y * PH, w = box.w * PW, h = box.h * PH;
    const isSelected = selectedBox === id;
    let g = `<g class="text-box ${isSelected ? 'selected' : ''}" data-box="${id}">`;
    g += `<rect class="bg" x="${x}" y="${y}" width="${w}" height="${h}"/>`;

    const lines = wrapTextSVG(text, fontSize, w - 16, isBold);
    const lineHeight = fontSize * 1.25;
    const pad = 8;
    let ty = y + fontSize + pad;

    let anchor = "start", textX = x + pad;
    if (align === "center") { anchor = "middle"; textX = x + w / 2; }
    else if (align === "right") { anchor = "end"; textX = x + w - pad; }

    for (const line of lines) {
        if (ty > y + h - 4) break;
        g += `<text x="${textX}" y="${ty}" font-size="${fontSize}" text-anchor="${anchor}" class="${isBold ? 'title-text' : ''}">${escHtml(line)}</text>`;
        ty += lineHeight;
    }

    const hs = 12;
    g += `<rect class="resize-handle" data-box="${id}" x="${x + w - hs}" y="${y + h - hs}" width="${hs}" height="${hs}"/>`;
    g += `</g>`;
    return g;
}

function wrapTextSVG(text, fontSize, maxWidth, isBold) {
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
    const rect = svg.getBoundingClientRect();
    return { x: (e.clientX - rect.left) / displayScale, y: (e.clientY - rect.top) / displayScale };
}

function onMouseDown(e) {
    const handle = e.target.closest(".resize-handle");
    const box = e.target.closest(".text-box");
    const bgDrag = e.target.closest(".bg-drag");
    const pt = getSVGPoint(e);
    const p = prompts[currentIdx];
    const L = layouts[p.id];

    if (box) {
        const bid = box.getAttribute("data-box");
        if (!bid) return;
        selectedBox = bid;
        const bx = L[bid + "_box"];

        if (handle) {
            dragState = { mode: "resize", boxId: bid, startX: pt.x, startY: pt.y, origW: bx.w, origH: bx.h };
        } else {
            dragState = { mode: "move", boxId: bid, startX: pt.x, startY: pt.y, origX: bx.x, origY: bx.y };
        }
        e.preventDefault();
        renderPage();
    } else if (bgDrag) {
        // Image pan mode
        selectedBox = null;
        dragState = {
            mode: "pan",
            startX: pt.x,
            startY: pt.y,
            origOX: L.img_offset_x,
            origOY: L.img_offset_y,
        };
        e.preventDefault();
        renderPage();
    }
}

function onMouseMove(e) {
    if (!dragState) return;
    const pt = getSVGPoint(e);
    const p = prompts[currentIdx];
    const L = layouts[p.id];

    if (dragState.mode === "pan") {
        const geo = imgGeometry(p);
        if (geo.extraX > 0) {
            const dxFrac = -(pt.x - dragState.startX) / geo.extraX;
            L.img_offset_x = clamp(dragState.origOX + dxFrac, 0, 1);
        }
        if (geo.extraY > 0) {
            const dyFrac = -(pt.y - dragState.startY) / geo.extraY;
            L.img_offset_y = clamp(dragState.origOY + dyFrac, 0, 1);
        }
    } else {
        const dx = (pt.x - dragState.startX) / PW;
        const dy = (pt.y - dragState.startY) / PH;
        const bx = L[dragState.boxId + "_box"];
        if (dragState.mode === "move") {
            bx.x = clamp(dragState.origX + dx, 0, 1 - bx.w);
            bx.y = clamp(dragState.origY + dy, 0, 1 - bx.h);
        } else {
            bx.w = clamp(dragState.origW + dx, 0.1, 1 - bx.x);
            bx.h = clamp(dragState.origH + dy, 0.05, 1 - bx.y);
        }
    }

    renderPage();
    e.preventDefault();
}

function onMouseUp() { dragState = null; }

function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

// ── PDF generation ────────────────────────────────────────────────────
async function generatePDF() {
    const btn = document.getElementById("generate-btn");
    const status = document.getElementById("status");
    btn.disabled = true;
    btn.textContent = "Generating...";
    status.textContent = "";

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
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
    if (e.key === "ArrowLeft") prevPage();
    if (e.key === "ArrowRight") nextPage();
    if (e.key === "Escape") { selectedBox = null; renderPage(); }
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
