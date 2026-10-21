#!/usr/bin/env python3
"""DUT map of workshop_inverter.gds (images/dut_map.png / .svg) coloured by submission status.

The block positions (DUT frames, mux_ch, decoder, VCO, logo, pins) come from the pristine top
layout; each inv_dut_NNN is coloured from state/state.json:
  green   imported (and its newest response passed)
  yellow  imported, but a newer response for it failed (the imported one is kept)
  red     responses came in, but none was imported
  blue    no response yet

Usage:  python3 dut_map.py [--state state/state.json] [--gds base/workshop_inverter_base.gds]
            [OUT.png [OUT.svg]]     (default: dut_map.png / dut_map.svg here)
"""
import argparse
import datetime
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
TOP = "workshop_inverter"
FAILED = ("key_mismatch", "download_error", "dut_check_error", "top_check_error")

# (fill, edge, text) per status
COLORS = {"imported": ("#c8ecc4", "#2e8b3a", "#14532d"),
          "failed_after_import": ("#fff1a8", "#c9a000", "#6b5400"),
          "failed": ("#f8c9c4", "#c0392b", "#7f1d1d"),
          "none": ("#cfe0fb", "#2a5db0", "#123a7a")}
LABELS = {"imported": "imported", "failed_after_import": "imported, newer submission failed",
          "failed": "failed (not imported)", "none": "not submitted"}


def response_time(r):
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M:%S"):
        try:
            return datetime.datetime.strptime(r["time"], fmt), r.get("row", 0)
        except ValueError:
            pass
    return datetime.datetime.min, r.get("row", 0)


def dut_status(state):
    """{dut_num: status key of COLORS} for the DUTs with responses."""
    by_dut = {}
    for r in state.get("responses", {}).values():
        if r.get("dut") is not None:
            by_dut.setdefault(int(r["dut"]), []).append(r)
    imported = {int(n) for n in state.get("imported", {})}
    out = {}
    for n, rs in by_dut.items():
        newest = max(rs, key=response_time)
        if n in imported:
            out[n] = "failed_after_import" if newest["status"] in FAILED else "imported"
        elif any(r["status"] in FAILED for r in rs):
            out[n] = "failed"
    for n in imported:
        out.setdefault(n, "imported")
    return out


def geometry(gds):
    import klayout.db as db
    ly = db.Layout()
    ly.read(gds)
    top = ly.cell(TOP)
    g = {"chip": top.dbbox(), "dut": {}, "mux": [], "pins": []}
    for inst in top.each_inst():
        name, box = inst.cell.name, inst.dbbox()
        m = re.fullmatch(r"inv_dut_(\d{3})", name)
        if m:
            g["dut"][int(m.group(1))] = box
        elif name == "mux_ch":
            g["mux"].append(box)
        elif name in ("decoder_7to128", "vco_only") or name.startswith("ISHIKAI"):
            g[name if not name.startswith("ISHIKAI") else "logo"] = box
    for li in ly.layer_indexes():
        if ly.get_info(li).datatype == 10:   # pin labels
            for sh in top.shapes(li).each():
                if sh.is_text():
                    g["pins"].append((sh.text.string, sh.dtext.x, sh.dtext.y))
    return g


def draw(state, gds, png, svg=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch, Rectangle
    plt.rcParams["svg.hashsalt"] = TOP   # stable ids: an unchanged map gives an identical file
    g = geometry(gds)
    st = dut_status(state)
    fig, ax = plt.subplots(figsize=(10, 13.5))

    def rect(b, **kw):
        ax.add_patch(Rectangle((b.left, b.bottom), b.width(), b.height(), **kw))

    c = g["chip"]
    rect(c, fill=False, ec="#333333", lw=1.5)
    if "logo" in g:
        rect(g["logo"], fc="#f3f3f3", ec="#999999")
        ax.text(g["logo"].center().x, g["logo"].center().y + 25, "logo (METAL5)", ha="center", va="center",
                fontsize=10, color="#333333")
    if "vco_only" in g:
        rect(g["vco_only"], fc="#d6ecd2", ec="#4a9a44")
        ax.text(g["vco_only"].center().x, g["vco_only"].center().y, "vco_only", ha="center", va="center",
                fontsize=10, color="#333333")
    if "decoder_7to128" in g:
        rect(g["decoder_7to128"], fc="#fde2c4", ec="#d98a2b")
        ax.text(g["decoder_7to128"].center().x, g["decoder_7to128"].center().y, "decoder_7to128\n(7→128)",
                ha="center", va="center", fontsize=10, color="#333333")
    for b in g["mux"]:
        rect(b, fc="#e8e8e8", ec="#cccccc", lw=0.6)
    for n, b in g["dut"].items():
        fc, ec, tc = COLORS[st.get(n, "none")]
        rect(b, fc=fc, ec=ec, lw=1.2)
        ax.text(b.center().x, b.center().y, f"{n:03d}", ha="center", va="center", rotation=90,
                fontsize=8, fontweight="bold", color=tc)
    rows = sorted({round(b.center().y, 3) for b in g["dut"].values()}, reverse=True)
    cols = sorted({round(b.center().x, 3) for b in g["dut"].values()})
    top_y = max(b.top for b in g["mux"] + list(g["dut"].values()))
    for i, x in enumerate(cols):
        ax.text(x, top_y + 8, f"+{i}", ha="center", va="bottom", fontsize=7, color="#555555")
    left_x = min(b.left for b in g["mux"] + list(g["dut"].values()))
    for i, y in enumerate(rows):
        ax.text(left_x - 12, y, f"{16 * i:03d}–", ha="right", va="center", fontsize=7, color="#555555")
    pins, bus = [], {}
    for name, x, y in g["pins"]:   # D[0] .. D[6] -> one D[6:0]
        m = re.fullmatch(r"(\w+)\[(\d+)\]", name)
        if m:
            bus.setdefault(m.group(1), []).append((int(m.group(2)), x, y))
        else:
            pins.append((name, x, y))
    for base, bits in bus.items():
        bits.sort()
        pins.append((f"{base}[{bits[-1][0]}:{bits[0][0]}]", sum(b[1] for b in bits) / len(bits),
                     sum(b[2] for b in bits) / len(bits)))
    for name, x, y in pins:
        ax.plot([x], [y], "o", color="#c0392b", ms=3)
    sides = {"top": [], "bottom": [], "left": [], "right": []}
    for p in pins:
        side = ("top" if p[2] > c.top - 5 else "bottom" if p[2] < c.bottom + 5
                else "left" if p[1] < c.left + 5 else "right")
        sides[side].append(p)
    for side, ps in sides.items():   # spread the labels of pins that are close together
        along = 1 if side in ("top", "bottom") else 2
        ps.sort(key=lambda p: p[along])
        pos, last = [], None
        for p in ps:
            v = p[along] if last is None else max(p[along], last + (45 if along == 1 else 8))
            pos.append(v)
            last = v
        shift = (pos[-1] - ps[-1][along]) / 2 if ps else 0
        for p, v in zip(ps, pos):
            name, x, y = p
            if side == "top":
                xy, ha, va = (v - shift, y + 18), "center", "bottom"
            elif side == "bottom":
                xy, ha, va = (v - shift, y - 18), "center", "top"
            elif side == "left":
                xy, ha, va = (x - 10, v - shift), "right", "center"
            else:
                xy, ha, va = (x + 10, v - shift), "left", "center"
            ax.annotate(name, (x, y), xy, fontsize=7, color="#c0392b", ha=ha, va=va,
                        arrowprops={"arrowstyle": "-", "color": "#c0392b", "lw": 0.6})
    counts = {k: sum(v == k for v in st.values()) for k in COLORS}
    counts["none"] = len(g["dut"]) - len(st)
    handles = [Patch(fc=COLORS[k][0], ec=COLORS[k][1], label=f"inv_dut_NNN: {LABELS[k]} ({counts[k]})")
               for k in ("imported", "failed_after_import", "failed", "none")]
    handles += [Patch(fc="#e8e8e8", ec="#cccccc", label="mux_ch (TG MUX, per channel)"),
                Patch(fc="#fde2c4", ec="#d98a2b", label="decoder_7to128"),
                Patch(fc="#d6ecd2", ec="#4a9a44", label="vco_only")]
    grid_top = top_y + 25   # free space between the VCO / decoder and the DUT grid
    ax.legend(handles=handles, loc="lower left", bbox_to_anchor=(c.left + 12, grid_top),
              bbox_transform=ax.transData, fontsize=8)
    ax.set_xlim(c.left - 40, c.right + 40)
    ax.set_ylim(c.bottom - 35, c.top + 35)
    ax.set_aspect("equal")
    ax.set_xlabel("X [µm]")
    ax.set_ylabel("Y [µm]")
    ax.set_title(f"{TOP}.gds  DUT map (inv_dut_NNN, {len(cols)} × {len(rows)})\n"
                 "DUT number = D[6:0] = 16·row + col  (top-left 000 → bottom-right 127)", fontsize=12)
    fig.tight_layout()
    fig.savefig(png, dpi=200, metadata={"Software": None})
    if svg:
        fig.savefig(svg, metadata={"Date": None, "Creator": None})
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--state", default=os.path.join(HERE, "state", "state.json"))
    ap.add_argument("--gds", default=os.path.join(HERE, "base", f"{TOP}_base.gds"))
    ap.add_argument("png", nargs="?", default="dut_map.png")
    ap.add_argument("svg", nargs="?", default="dut_map.svg")
    args = ap.parse_args()
    state = json.load(open(args.state, encoding="utf-8")) if os.path.exists(args.state) else {}
    draw(state, args.gds, args.png, args.svg)


if __name__ == "__main__":
    main()
