#!/usr/bin/env python3
"""Render the README's preview and cheatsheet from the default layout.

    tools/graphics.py            # writes preview.png and docs/cheatsheet.png

Both images are built from padd's own DEFAULT_LAYERS, so they cannot drift
from what the plugin ships. Needs chromium and the CaskaydiaMono Nerd Font.
"""

from __future__ import annotations

import html
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from padd.config import Config  # noqa: E402

FACE = {"A": "#5cb85c", "B": "#e0533d", "X": "#3d8fe0", "Y": "#e8b830"}
CAP = {
    "BACK": "◀", "START": "▶", "GUIDE": "\U000F05B9",
    "UP": "↑", "DOWN": "↓", "LEFT": "←", "RIGHT": "→",
}
ORDER = ["A", "B", "X", "Y", "UP", "DOWN", "LEFT", "RIGHT", "LB", "RB", "START", "BACK", "L3", "R3", "GUIDE"]

STYLE = """
:root { --bg:#0b0d0d; --ink:#e7e9e8; --dim:#8b918f; --faint:#5a605e; --line:#262b2a;
        --accent:#6cc4a4; --panel:#050606; }
* { box-sizing:border-box; margin:0; padding:0; }
body { width:%(w)dpx; height:%(h)dpx; background:var(--bg); color:var(--ink);
       font-family:'CaskaydiaMono Nerd Font','CaskaydiaMono NF',monospace; overflow:hidden;
       background-image: radial-gradient(ellipse 60%% 50%% at 30%% 0%%, rgba(108,196,164,.10), transparent 70%%),
         linear-gradient(var(--line) 1px, transparent 1px), linear-gradient(90deg, var(--line) 1px, transparent 1px);
       background-size: auto, 48px 48px, 48px 48px; background-position: 0 0, -1px -1px, -1px -1px; }
body::after { content:''; position:fixed; inset:0; background:radial-gradient(ellipse at center, transparent 40%%, var(--bg) 95%%); pointer-events:none; }
.cap { display:inline-flex; align-items:center; justify-content:center; width:34px; height:34px; flex:none;
       border-radius:7px; background:rgba(231,233,232,.07); color:#d6d9d8; font-size:13px; font-weight:600; }
.cap.face { border-radius:50%%; font-size:17px; font-weight:800; background:transparent; border:1.5px solid; }
.cap.dpad { font-size:20px; } .cap.logo { font-size:21px; } .cap.sys { font-size:14px; }
.cap.sm { width:28px; height:28px; font-size:11px; border-radius:6px; } .cap.sm.face { font-size:14px; }
.cap.sm.dpad { font-size:17px; } .cap.sm.logo { font-size:18px; }
.cap.lit { background:var(--accent); color:#06120d; }
.eyebrow { color:var(--dim); font-size:17px; letter-spacing:.22em; }
.dot { color:var(--accent); }
.card { background:var(--panel); border:1.5px solid #343a39; border-radius:6px; }
.dim { color:var(--dim); } .faint { color:var(--faint); }
"""


def cap(button: str, small: bool = False, lit: bool = False) -> str:
    text = CAP.get(button, button)
    classes = ["cap"]
    style = ""
    if button in FACE:
        classes.append("face")
        style = f' style="color:{FACE[button]};border-color:{FACE[button]}"'
        if lit:
            style = f' style="background:{FACE[button]};color:#06120d;border-color:{FACE[button]}"'
    elif button in ("UP", "DOWN", "LEFT", "RIGHT"):
        classes.append("dpad")
    elif button == "GUIDE":
        classes.append("logo")
    elif button in ("BACK", "START"):
        classes.append("sys")
    if small:
        classes.append("sm")
    if lit and button not in FACE:
        classes.append("lit")
    return f'<span class="{" ".join(classes)}"{style}>{html.escape(text)}</span>'


def entries(layer) -> list[tuple[str, str, str]]:
    """(button, label, how) for a layer, in pad order."""
    out = []
    for button in ORDER:
        binding = layer.bindings.get(button)
        if binding is None:
            continue
        if binding.tap is not None:
            how = "repeats" if binding.tap.repeat else ""
            hold = f"hold: {binding.hold.label}" if binding.hold is not None else ""
            out.append((button, binding.tap.label, hold or how))
        elif binding.hold is not None:
            out.append((button, binding.hold.label, "hold"))
    return out


def render(page: str, out: Path, width: int, height: int) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "page.html"
        src.write_text(page)
        subprocess.run(
            ["chromium", "--headless=new", "--disable-gpu", "--hide-scrollbars",
             "--force-device-scale-factor=1", f"--window-size={width},{height}",
             f"--screenshot={out}", src.as_uri()],
            check=True, capture_output=True,
        )
    print(f"wrote {out.relative_to(ROOT)}")


# -- preview -----------------------------------------------------------------


def preview(config: Config) -> str:
    w, h = 1600, 1000
    text = config.layer("text")
    rows = {r[0]: r for r in entries(text)}
    # Laid out as the pad is held, as the real card is.
    left = [rows[b] for b in ("LB", "UP", "DOWN", "LEFT", "RIGHT", "BACK") if b in rows]
    right = [rows[b] for b in ("RB", "Y", "X", "B", "A", "START") if b in rows]
    system = {r[0]: r for r in entries(config.layer("system"))}
    found = [system[b] for b in ("UP", "DOWN") if b in system]

    def hint_rows(items):
        return "".join(
            f'<div class="hr">{cap(b, lit=(b == "B"))}<div><div>{html.escape(l)}</div>'
            + (f'<div class="how">{html.escape(how)}</div>' if how else "")
            + "</div></div>"
            for b, l, how in items
        )

    features = [
        ("\U000F0BB4", "Sticks are a mouse", "fine near the centre, fast at full tilt; the other stick scrolls"),
        ("\U000F0E36", "Triggers hold layers", "LT for text editing, RT for windows, tabs and volume"),
        ("\U000F05B9", "Learn it on the pad", "hold a trigger and this card shows what every button does"),
        ("\U000F0E3C", "One meaning per button", "B goes back, then deletes, then closes, in each layer"),
    ]
    feat = "".join(
        f'<li><span class="fi">{g}</span><div><b>{t}</b><br><span class="dim">{d}</span></div></li>'
        for g, t, d in features
    )
    style = STYLE % {"w": w, "h": h} + """
      .wrap { position:relative; z-index:1; display:flex; gap:70px; padding:96px 96px 0 110px; }
      .copy { width:640px; }
      h1 { font-size:76px; line-height:1.06; margin:26px 0 30px; font-weight:700; letter-spacing:-.01em; }
      .lede { font-size:23px; line-height:1.55; color:#c3c7c6; }
      ul { list-style:none; margin-top:44px; display:grid; gap:24px; font-size:19px; line-height:1.45; }
      li { display:flex; gap:18px; } .fi { color:var(--accent); font-size:24px; width:28px; text-align:center; }
      .stage { position:relative; flex:1; padding-top:20px; }
      .hint { padding:30px 34px 32px; width:690px; }
      .hhead { display:flex; align-items:center; gap:14px; font-size:28px; font-weight:700; margin-bottom:26px; }
      .cols { display:grid; grid-template-columns:1fr 1fr; gap:6px 30px; }
      .col { display:grid; gap:15px; align-content:start; }
      .hr { display:flex; gap:16px; align-items:center; font-size:20px; }
      .how { color:var(--dim); font-size:14px; margin-top:2px; }
      .strip { display:flex; gap:14px; margin-top:30px; }
      .chip { padding:16px 20px; font-size:17px; display:flex; align-items:center; gap:12px; }
      .chip .k { display:flex; gap:6px; align-items:center; }
      .search { margin-top:22px; width:690px; padding:22px 26px 14px; }
      .field { border:1.5px solid #3a403f; border-radius:4px; padding:12px 16px; font-size:18px; display:flex; justify-content:space-between; }
      .field .caret { color:var(--accent); }
      .tabs { display:flex; gap:22px; font-size:16px; color:var(--dim); margin:16px 2px 12px; align-items:center; }
      .tabs .on { color:var(--ink); font-weight:700; border-bottom:2px solid var(--accent); padding-bottom:5px; }
      .sec { font-size:13px; letter-spacing:.12em; color:var(--accent); margin:4px 2px 6px; font-weight:700; }
      .row { display:flex; align-items:center; gap:14px; padding:8px 4px; font-size:18px; }
      .row .l { flex:1; } .row .k { color:var(--faint); font-size:15px; } .row .how { color:var(--accent); font-size:13px; }
    """
    return f"""<!doctype html><meta charset="utf-8"><style>{style}</style>
<div class="wrap">
  <div class="copy">
    <div class="eyebrow">OMARCHY-CONTROLLER</div>
    <h1>Your desktop,<br>from the sofa<span class="dot">.</span></h1>
    <p class="lede">A game controller as a mouse, a scroll wheel and every shortcut you use.
      Hold a trigger and the buttons change job, with a card on screen saying how.</p>
    <ul>{feat}</ul>
  </div>
  <div class="stage">
    <div class="card hint">
      <div class="hhead">{cap("LT", lit=True)}Text</div>
      <div class="cols"><div class="col">{hint_rows(left)}</div><div class="col">{hint_rows(right)}</div></div>
    </div>
    <div class="strip">
      <div class="card chip"><span class="k">{cap("GUIDE", True)}<span class="faint">+</span>{cap("BACK", True)}</span>everything off for a game</div>
      <div class="card chip"><span class="k">{cap("RT", True)}</span>windows, tabs, volume</div>
    </div>
    <div class="card search">
      <div class="field"><span>vol<span class="caret">|</span></span><span class="faint">+</span></div>
      <div class="tabs"><span class="on">All</span><span>Browse</span><span>Text</span><span>System</span></div>
      <div class="sec">SYSTEM · HOLD RT</div>
      {"".join(f'<div class="row">{cap(b, True)}<span class="l">{html.escape(l)}</span><span class="how">{html.escape(how)}</span><span class="k">{k}</span></div>'
               for (b, l, how), k in zip(found, ("VOLUMEUP", "VOLUMEDOWN")))}
    </div>
  </div>
</div>""", w, h


# -- cheatsheet --------------------------------------------------------------


def cheatsheet(config: Config) -> str:
    w, h = 1600, 1180
    columns = []
    for layer in config.layers:
        rows = "".join(
            f'<div class="r">{cap(b)}<div class="l">{html.escape(l)}</div>'
            + (f'<div class="how">{html.escape(how)}</div>' if how else "")
            + "</div>"
            for b, l, how in entries(layer)
        )
        head = (f'{cap(layer.key, lit=True)}<div><div class="ln">{html.escape(layer.name)}</div>'
                f'<div class="dim small">hold {layer.key}</div></div>') if layer.key else (
                f'<div class="cap" style="font-size:20px">\U000F05BA</div><div><div class="ln">{html.escape(layer.name)}</div>'
                f'<div class="dim small">no trigger held</div></div>')
        columns.append(f'<div class="card col"><div class="ch">{head}</div>{rows}</div>')
    style = STYLE % {"w": w, "h": h} + """
      .wrap { position:relative; z-index:1; padding:70px 80px; }
      .top { display:flex; align-items:flex-end; justify-content:space-between; margin-bottom:34px; }
      h1 { font-size:54px; font-weight:700; margin-top:14px; }
      .rule { font-size:19px; color:#c3c7c6; max-width:560px; line-height:1.5; text-align:right; }
      .grid { display:grid; grid-template-columns:repeat(3, 1fr); gap:24px; }
      .col { padding:26px 26px 22px; display:grid; gap:12px; align-content:start; }
      .ch { display:flex; gap:14px; align-items:center; padding-bottom:18px; margin-bottom:6px; border-bottom:1px solid var(--line); }
      .ln { font-size:24px; font-weight:700; } .small { font-size:14px; margin-top:2px; }
      .r { display:flex; align-items:center; gap:14px; font-size:18px; }
      .l { flex:1; } .how { font-size:13px; color:var(--accent); opacity:.85; }
      .foot { display:grid; grid-template-columns:repeat(4, 1fr); gap:24px; margin-top:24px; }
      .f { padding:20px 22px; font-size:16px; line-height:1.45; }
      .f b { display:flex; align-items:center; gap:10px; font-size:17px; margin-bottom:8px; }
    """
    foot = [
        (cap("LS", True) + cap("RS", True), "Sticks", "Left points, right scrolls. L3 slows the pointer for small targets."),
        (cap("GUIDE", True), "Guide", "Tap: show the current layer on screen. Hold: mouse off for a game."),
        (cap("GUIDE", True) + '<span class="faint">+</span>' + cap("BACK", True), "Switch it all", "Off and back on, even while it is off."),
        (cap("LT", True) + cap("A", True), "Falls through", "A layer leaves a button alone? It does its Browse job."),
    ]
    feet = "".join(f'<div class="card f"><b>{k}{t}</b><span class="dim">{d}</span></div>' for k, t, d in foot)
    return f"""<!doctype html><meta charset="utf-8"><style>{style}</style>
<div class="wrap">
  <div class="top">
    <div><div class="eyebrow">CHEATSHEET</div><h1>Three layers, one pad<span class="dot">.</span></h1></div>
    <div class="rule">Each button keeps one idea in every layer: <b>B</b> backs out,
      <b>LB / RB</b> step between things, the <b>d-pad</b> moves.</div>
  </div>
  <div class="grid">{"".join(columns)}</div>
  <div class="foot">{feet}</div>
</div>""", w, h


def main() -> int:
    config = Config({})
    page, w, h = preview(config)
    render(page, ROOT / "preview.png", w, h)
    page, w, h = cheatsheet(config)
    (ROOT / "docs").mkdir(exist_ok=True)
    render(page, ROOT / "docs" / "cheatsheet.png", w, h)
    return 0


if __name__ == "__main__":
    sys.exit(main())
