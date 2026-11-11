"""Pictures of a part's pin support (pin_support.py): plan of the pins over the spots they hold, and the cage.

    python render_pin_support.py PART.stl [--out DIR] [--cut-y 0]   (after pin_support.py, same --out)

Writes DIR/renders/pin_plan.png and pin_cage.png.
"""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import trimesh
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

import pin_support

BRASS, PART, NAVY = '#b08d57', '#c9ced6', '#1f2d4a'


def shaded(ax, m, color, alpha=1.0, light=(0.4, -0.6, 0.7)):
    k = np.clip(m.face_normals @ np.array(light) / np.linalg.norm(light), 0.1, 1)
    rgb = np.array(matplotlib.colors.to_rgb(color))
    ax.add_collection3d(Poly3DCollection(m.triangles, facecolors=np.column_stack([rgb * (0.35 + 0.65 * k[:, None]), np.full(len(k), alpha)]),
                                         edgecolor='none'))


def keep(m, cut_y):
    """the faces behind y = cut_y; big faces split first so the cut edge is straight"""
    m = m.subdivide_to_size(2.0)
    m.update_faces(m.triangles_center[:, 1] > cut_y)
    m.remove_unreferenced_vertices()
    return m


def render(part, out, cut_y=0.0):
    part, out = Path(part), Path(out)
    info = json.loads((out / 'build_info.json').read_text())
    r = out / 'renders'
    r.mkdir(parents=True, exist_ok=True)
    t = trimesh.load(part, force='mesh')
    p = pin_support.Pins(t, info['keep_out'], **info['settings'])
    xy, h, _ = p.candidates()
    pins = np.load(out / 'pins.npy')
    lo, hi = t.bounds

    fig, ax = plt.subplots(figsize=(11, 9.5))
    sec = t.section([0, 0, 1], [0, 0, 0.2])
    for e in (sec.discrete if sec is not None else []):
        ax.fill(e[:, 0], e[:, 1], color='#b0b4ba', lw=0)
    sc = ax.scatter(xy[:, 0], xy[:, 1], c=h, s=1, cmap='viridis')
    ax.scatter(pins[:, 0], pins[:, 1], s=28, facecolors='none', edgecolors='#d03010', lw=1.2)
    for a, b, c, d in info['keep_out']:
        ax.add_patch(plt.Rectangle((min(a, c), min(b, d)), abs(c - a), abs(d - b), fill=False, ls='--', ec=NAVY))
    ax.set_xlim(lo[0] - 3, hi[0] + 3); ax.set_ylim(lo[1] - 3, hi[1] + 3)
    ax.set_aspect('equal'); fig.colorbar(sc, label='height of the overhang the pin holds, mm')
    ax.set_title(f"{info['title']}, seen from above: {len(pins)} pins (red) over every spot flatter than "
                 f"{info['settings']['overhang']:.0f} deg (dots); grey: footprint on the bed"
                 + ('; dashed: no pins' if info['keep_out'] else ''), fontsize=10, color=NAVY)
    fig.tight_layout(); fig.savefig(r / 'pin_plan.png', dpi=90); plt.close(fig)

    sup = trimesh.load(part.with_name(part.stem + '_pins.stl'), force='mesh')
    fig = plt.figure(figsize=(15, 8))
    for i, title in enumerate(('pin cage alone', f'in the part, cut at y = {cut_y:g} (front half removed)')):
        ax = fig.add_subplot(1, 2, i + 1, projection='3d')
        if i:
            shaded(ax, keep(t, cut_y), PART)
            shaded(ax, keep(sup, cut_y - 2), BRASS)
        else:
            shaded(ax, sup, BRASS)
        ax.set_xlim(lo[0], hi[0]); ax.set_ylim(lo[1], hi[1]); ax.set_zlim(0, hi[2])
        ax.set_box_aspect(tuple(hi - lo))
        ax.view_init(elev=12, azim=-60); ax.set_title(title, color=NAVY); ax.set_axis_off()
    fig.suptitle(f"{info['title']}: {info['pins']} pins, {info['braces']} braces, about {info['support_g_petg']:.0f} g PETG",
                 color=NAVY)
    fig.tight_layout(); fig.savefig(r / 'pin_cage.png', dpi=90); plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('part')
    ap.add_argument('--out')
    ap.add_argument('--cut-y', type=float, default=0.0)
    a = ap.parse_args()
    part = Path(a.part)
    render(part, a.out or part.with_name(part.stem + '_pin_support'), a.cut_y)


if __name__ == '__main__':
    main()
