"""Pictures of a part's trunk support (trunk_support.py): plan of the posts and the pins they carry, and the trees.

    python render_trunk_support.py PART.stl [--out DIR] [--cut-y 0]   (after trunk_support.py, same --out)

Writes DIR/renders/trunk_plan.png and trunk_trees.png.
"""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import trimesh

import trunk_support
from render_pin_support import BRASS, NAVY, PART, keep, shaded


def render(part, out, cut_y=0.0):
    part, out = Path(part), Path(out)
    info = json.loads((out / 'build_info.json').read_text())
    tr = json.loads((out / 'trunks.json').read_text())
    r = out / 'renders'
    r.mkdir(parents=True, exist_ok=True)
    t = trimesh.load(part, force='mesh')
    pins = np.array(tr['pins'])
    lo, hi = t.bounds

    fig, ax = plt.subplots(figsize=(11, 9.5))
    sec = t.section([0, 0, 1], [0, 0, 0.2])
    for e in (sec.discrete if sec is not None else []):
        ax.fill(e[:, 0], e[:, 1], color='#b0b4ba', lw=0)
    for post in tr['posts']:
        cx, cy = post['xy']
        for i in post['pins']:
            ax.plot([cx, pins[int(i), 0]], [cy, pins[int(i), 1]], color=BRASS, lw=1.2)
        ax.add_patch(plt.Circle((cx, cy), trunk_support.TRUNK_FOOT_D / 2, color=BRASS, alpha=0.5, lw=0))
    ax.scatter(pins[:, 0], pins[:, 1], s=18, color='#d03010', zorder=3)
    if tr['alone']:
        al = pins[tr['alone']]
        ax.scatter(al[:, 0], al[:, 1], s=60, facecolors='none', edgecolors=NAVY, lw=1.4, zorder=4)
    for a, b, c, d in info['keep_out']:
        ax.add_patch(plt.Rectangle((min(a, c), min(b, d)), abs(c - a), abs(d - b), fill=False, ls='--', ec=NAVY))
    ax.set_xlim(lo[0] - 3, hi[0] + 3); ax.set_ylim(lo[1] - 3, hi[1] + 3)
    ax.set_aspect('equal')
    ax.set_title(f"{info['title']}, seen from above: {info['posts']} posts (brass feet) carry {info['pins_on_posts']} "
                 f"pin points (red);\n{info['pins_alone']} points keep their own column (navy rings); grey: footprint on the bed",
                 fontsize=10, color=NAVY)
    fig.tight_layout(); fig.savefig(r / 'trunk_plan.png', dpi=90); plt.close(fig)

    sup = trimesh.load(part.with_name(part.stem + '_trunks.stl'), force='mesh')
    fig = plt.figure(figsize=(15, 8))
    for i, title in enumerate(('trunks alone', f'in the part, cut at y = {cut_y:g} (front half removed)')):
        ax = fig.add_subplot(1, 2, i + 1, projection='3d')
        if i:
            shaded(ax, keep(t, cut_y), PART)
            shaded(ax, keep(sup, cut_y - 2), BRASS)
        else:
            shaded(ax, sup, BRASS)
        ax.set_xlim(lo[0], hi[0]); ax.set_ylim(lo[1], hi[1]); ax.set_zlim(0, hi[2])
        ax.set_box_aspect(tuple(hi - lo))
        ax.view_init(elev=12, azim=-60); ax.set_title(title, color=NAVY); ax.set_axis_off()
    fig.suptitle(f"{info['title']}: {info['posts']} posts, {info['pins']} points, about {info['support_g_petg']:.0f} g PETG",
                 color=NAVY)
    fig.tight_layout(); fig.savefig(r / 'trunk_trees.png', dpi=90); plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('part')
    ap.add_argument('--out')
    ap.add_argument('--cut-y', type=float, default=0.0)
    a = ap.parse_args()
    part = Path(a.part)
    render(part, a.out or part.with_name(part.stem + '_trunk_support'), a.cut_y)


if __name__ == '__main__':
    main()
