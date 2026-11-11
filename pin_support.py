"""Thumbtack pin support for any part in its print orientation: your own support instead of the slicer's trees.

    python pin_support.py PART.stl [--keep-out X0 Y0 X1 Y1]... [--out DIR] [--spacing 10] ...

The part must already sit as it prints: bed at z 0, up +z. Rays go up from the bed on a GRID mm grid. Where the first
surface a ray meets faces down within OVERHANG deg of horizontal (40 by default; PrusaSlicer measures from the
horizontal too), that spot needs support. Like the slicer's "on build plate only", ceilings over the part itself get
none. Pins are picked greedily, flattest spots first, so that every such spot has a pin within SPACING mm. Each pin is:
- a TIP_D point on a 30 deg cone that stops TIP_GAP (one 0.20 mm layer) under the lowest ceiling point over it, like
  the slicer's top contact Z distance: the ceiling's first layer sits on the points and they pop off clean
  (lowered where a wall or step beside the tip comes within MIN_GAP of its cone);
- a COL_D column, clear of the part by CLEAR all the way up to the cone (checked with rays round it);
- a FOOT_D foot on the bed for grip (COL_D + 1 where the part's own footprint is that close).
Isolated specks (less than MIN_PATCH of the PATCH mm square round them) and spots under MIN_H are left: they print
as they are. Neighbouring pins (Delaunay, up to BRACE_MAX apart) are tied by BRACE bars that climb at BRACE_ANGLE deg
from one pin to the other, a new one every BRACE_STEP mm in alternate directions. Steeper than an overhang, they
print without bridging, stop tall pins wobbling and hold the whole cage together, so it comes off in one piece.
Keep-out boxes (xy, print frame) are places no pin may stand, e.g. under a nut slot whose roof should bridge. A hole
that must stay empty under a roof full of holes needs a sacrificial skin in the part itself: skin() makes the disc.
Writes, next to the part: <stem>_pins.stl (support only, same frame: load both as one multi-part object) and
<stem>_with_pins.stl (merged, slice with supports off); in --out: build_info.json and pins.npy.
"""
import argparse
import json
from pathlib import Path

import manifold3d as mf
import numpy as np
import trimesh
from scipy import ndimage
from scipy.spatial import ConvexHull, Delaunay
from shapely.geometry import Point, Polygon




def M(t):
    """trimesh -> manifold"""
    return mf.Manifold(mf.Mesh(vert_properties=np.asarray(t.vertices, np.float32), tri_verts=np.asarray(t.faces, np.uint32)))


def from_manifold(m):
    """manifold -> trimesh, keeping manifold's own vertex sharing: re-merging float32 copies breaks thin walls"""
    g = m.to_mesh()
    return trimesh.Trimesh(g.vert_properties[:, :3], g.tri_verts, process=False)


DEFAULTS = dict(
    overhang=40.0,                             # PrusaSlicer's default threshold; steeper slopes print as they are
    spacing=10.0,
    min_patch=0.5,                             # share of the PATCH mm square round a spot that must overhang too
    patch=5,
    grid=1.0,
    min_h=4.0,                                 # lower overhangs print as they are
    tip_d=1.0, tip_gap=0.2,
    min_gap=0.05,                              # nothing of a tip closer to the part
    col_d=2.2, clear=0.6,
    foot_d=6.0, foot_h=0.6,
    brace=1.2, brace_step=20.0, brace_max=14.0, brace_first=10.0,
    brace_angle=55.0,                          # from horizontal; 45 still shows as overhang perimeter
)


def up_hits(t, xy, z0=0.01):
    """First surface above z0 at each xy: height and face index (inf, -1 where nothing)."""
    o = np.column_stack([xy, np.full(len(xy), z0)])
    loc, ri, fi = t.ray.intersects_location(o, np.tile([0, 0, 1.0], (len(o), 1)), multiple_hits=True)
    h, f = np.full(len(o), np.inf), np.full(len(o), -1)
    for k in np.argsort(-loc[:, 2]):            # keep the lowest hit per ray
        h[ri[k]], f[ri[k]] = loc[k, 2], fi[k]
    return h, f


def ring(x, y, r, n=12):
    return np.array([(x + r * np.cos(a), y + r * np.sin(a)) for a in np.linspace(0, 2 * np.pi, n, endpoint=False)])


def cone(z0, d0, z1, d1, x, y, seg=24):
    return mf.Manifold.cylinder(z1 - z0, d0 / 2, d1 / 2, seg).translate((x, y, z0))


def skin(x, y, z, r, thick=0.2):
    """A sacrificial disc, one layer thick, whose top is at z: closes a hole at a roof so the roof bridges."""
    return mf.Manifold.cylinder(thick, r, r, 48).translate((x, y, z - thick))


class Pins:
    def __init__(self, t, keep_out=(), **over):
        self.t, self.keep_out = t, [tuple(b) for b in keep_out]
        self.s = dict(DEFAULTS, **over)
        s = self.s
        self.tip_cone_h = (s['col_d'] - s['tip_d']) / 2 / np.tan(np.radians(30))

    def kept_out(self, x, y):
        return any(min(a, c) <= x <= max(a, c) and min(b, d) <= y <= max(b, d) for a, b, c, d in self.keep_out)

    def candidates(self):
        s, t = self.s, self.t
        lo, hi = t.bounds
        xs = np.arange(lo[0] + s['grid'] / 2, hi[0], s['grid'])
        ys = np.arange(lo[1] + s['grid'] / 2, hi[1], s['grid'])
        xy = np.array([(x, y) for x in xs for y in ys])
        h, f = up_hits(t, xy)
        ok = np.isfinite(h) & (h > s['min_h'])
        nz = np.where(f >= 0, t.face_normals[np.maximum(f, 0), 2], 0)
        tilt = np.degrees(np.arccos(np.clip(-nz, -1, 1)))   # face slope from horizontal: 0 = flat ceiling
        ok &= tilt < s['overhang']
        ok &= np.array([not self.kept_out(x, y) for x, y in xy])
        grid = ok.reshape(len(xs), len(ys)).astype(float)       # drop specks: surface texture prints as it is
        ok &= (ndimage.uniform_filter(grid, s['patch']) >= s['min_patch']).ravel()
        return xy[ok], h[ok], tilt[ok]

    def clear_column(self, x, y, h):
        """The column and its clearance ring meet nothing below the tip cone."""
        hr, _ = up_hits(self.t, ring(x, y, self.s['col_d'] / 2 + self.s['clear']), z0=-1.0)   # from under the bed: a wall standing on it counts
        return bool(np.all(hr > h - self.tip_cone_h - 1.0))

    def foot_d(self, x, y):
        """Full foot unless the part stands or hangs within reach of it; then a narrow one."""
        s = self.s
        top = s['foot_h'] + (s['foot_d'] - s['col_d']) / 2
        hr, _ = up_hits(self.t, ring(x, y, s['foot_d'] / 2 + s['clear'], 24), z0=-1.0)   # from under the bed: a wall standing on it counts
        return s['foot_d'] if np.all(hr > top + s['clear']) else s['col_d'] + 1.0

    def tip_top(self, x, y):
        """TIP_GAP under the lowest ceiling point over the tip disc (the ceiling may slope up to OVERHANG deg)."""
        d = self.s['tip_d']
        disc = [(x, y)] + [p for r in (d / 4, d / 2) for p in ring(x, y, r, 8)]
        return up_hits(self.t, np.array(disc))[0].min() - self.s['tip_gap']

    def tip_clear(self, x, y, top):
        """Lower the tip until its cone keeps MIN_GAP from the part (a wall or step just beside the tip disc);
        None if 2 mm lower is still too close."""
        s, c = self.s, self.tip_cone_h
        for drop in np.arange(0, 2.01, 0.1):
            z = top - drop
            pts = [(px, py, pz) for pz, d in ((z, s['tip_d']), (z - c / 2, (s['tip_d'] + s['col_d']) / 2), (z - c, s['col_d']))
                   for px, py in ring(x, y, d / 2, 16)]
            if trimesh.proximity.closest_point(self.t, np.array(pts))[1].min() >= s['min_gap']:
                return z
        return None

    def pin(self, x, y, top, foot):
        s, c = self.s, self.tip_cone_h
        return (cone(0, foot, s['foot_h'], foot, x, y) + cone(s['foot_h'], foot, s['foot_h'] + (foot - s['col_d']) / 2, s['col_d'], x, y)
                + cone(s['foot_h'], s['col_d'], top - c, s['col_d'], x, y) + cone(top - c, s['col_d'], top, s['tip_d'], x, y))

    def plan(self):
        """Pin spots (x, y, tip z, foot d), flattest first, and the spots they hold."""
        s = self.s
        xy, h, tilt = self.candidates()
        pins, blocked = [], 0
        for k in np.argsort(tilt):
            x, y = xy[k]
            if pins and min(np.hypot(px - x, py - y) for px, py, *_ in pins) < s['spacing']:
                continue
            top = self.tip_clear(x, y, self.tip_top(x, y)) if self.clear_column(x, y, h[k]) else None
            if top is None:
                blocked += 1
                continue
            pins.append((x, y, top, self.foot_d(x, y)))
        far = sum(1 for (x, y) in xy if not pins or min(np.hypot(px - x, py - y) for px, py, *_ in pins) > s['spacing'] * 1.5)
        return pins, dict(spots_needing_support=len(xy), spots_left_far_from_a_pin=far, columns_blocked=blocked)

    def braces(self, pins):
        s = self.s
        if len(pins) < 3:
            return []
        tri = Delaunay(np.array([(x, y) for x, y, *_ in pins]))
        edges = sorted({tuple(sorted((q[i], q[j]))) for q in tri.simplices for i, j in ((0, 1), (1, 2), (0, 2))})
        bars = []
        for a, b in edges:
            (xa, ya, ha, _), (xb, yb, hb, _) = pins[a], pins[b]
            d = np.hypot(xb - xa, yb - ya)
            if d > s['brace_max']:
                continue
            u = np.linspace(0, 1, 9)[1:-1]
            hs, _ = up_hits(self.t, np.column_stack([xa + u * (xb - xa), ya + u * (yb - ya)]), z0=-1.0)   # from under the bed: no bar through a wall standing on it
            top = min(ha, hb, hs.min()) - self.tip_cone_h - 2.0
            rise = d * np.tan(np.radians(s['brace_angle']))
            for i, z in enumerate(np.arange(s['brace_first'], top - s['brace'] - rise, s['brace_step'])):
                (x0, y0), (x1, y1) = ((xa, ya), (xb, yb))[::1 if i % 2 == 0 else -1]
                bars.append(mf.Manifold.batch_hull([cone(z, s['brace'], z + s['brace'], s['brace'], x0, y0, 12),
                                                    cone(z + rise, s['brace'], z + rise + s['brace'], s['brace'], x1, y1, 12)]))
        return bars

    def inside_footprint(self):
        """Inside the convex hull of the part's footprint on the bed: pins there touch surfaces facing inwards."""
        sec = self.t.section([0, 0, 1], [0, 0, 0.2])
        if sec is None:
            return lambda x, y: False
        v = sec.vertices[:, :2]
        hull = Polygon(v[ConvexHull(v).vertices])
        return lambda x, y: hull.contains(Point(x, y))


def clearance(t, sup_t, above):
    """Smallest gap from the support (away from the bed) to the part's surface."""
    v = sup_t.vertices[sup_t.vertices[:, 2] > above]
    if not len(v):
        return None
    _, d, _ = trimesh.proximity.closest_point(t, v)
    return float(d.min())


def run(part, out, keep_out=(), title=None, **over):
    part, out = Path(part), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    t = trimesh.load(part, force='mesh')
    if t.bounds[0][2] < -1e-3 or t.bounds[0][2] > 1e-3:
        raise SystemExit(f'{part.name}: bed at z {t.bounds[0][2]:.3f}, expected 0 (export it in its print orientation)')
    p = Pins(t, keep_out, **over)
    pins, cover = p.plan()
    parts = [p.pin(x, y, z, f) for x, y, z, f in pins] + p.braces(pins)
    sup = mf.Manifold.batch_boolean(parts, mf.OpType.Add)
    sup_t = from_manifold(sup)
    stem = part.with_suffix('')
    sup_t.export(f'{stem}_pins.stl')
    if t.is_watertight:
        shell = M(t)
        into = round((sup ^ shell).volume(), 3)
        both = from_manifold(shell + sup)
    else:                                        # meshes with errors: no boolean; the pins never touch the part
        into = None
        both = trimesh.util.concatenate([t, sup_t])
    both.export(f'{stem}_with_pins.stl')
    s = p.s
    gap = clearance(t, sup_t, s['foot_h'] + (s['foot_d'] - s['col_d']) / 2 + 0.5)
    hidden = p.inside_footprint()
    n_in = sum(1 for x, y, *_ in pins if hidden(x, y))
    reload = {k: trimesh.load(f'{stem}{k}.stl', force='mesh') for k in ('_pins', '_with_pins')}
    info = dict(part=part.name, title=title or part.stem, pins=len(pins), pins_inside_footprint=n_in,
                pins_outside_footprint=len(pins) - n_in, narrow_feet=sum(1 for *_, f in pins if f < s['foot_d']),
                braces=len(parts) - len(pins), **cover,
                support_cm3=round(sup.volume() / 1000, 1), support_g_petg=round(sup.volume() * 1.27e-3, 1),
                support_into_part_mm3=into, min_gap_support_to_part_mm=None if gap is None else round(gap, 3),
                tallest_pin_mm=round(max((z for _, _, z, _ in pins), default=0), 1),
                pieces_support=len(sup.decompose()), part_watertight=bool(t.is_watertight),
                reload_pins_watertight=bool(reload['_pins'].is_watertight),
                reload_with_pins_watertight=bool(reload['_with_pins'].is_watertight),
                keep_out=p.keep_out, settings=s)
    (out / 'build_info.json').write_text(json.dumps(info, indent=1))
    np.save(out / 'pins.npy', np.array(pins).reshape(-1, 4))
    print(json.dumps({k: v for k, v in info.items() if k != 'settings'}, indent=1))
    return info


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('part')
    ap.add_argument('--out', help='folder for build_info.json and pins.npy (default <stem>_pin_support next to the part)')
    ap.add_argument('--keep-out', nargs=4, type=float, action='append', default=[], metavar=('X0', 'Y0', 'X1', 'Y1'))
    ap.add_argument('--title')
    for k, v in DEFAULTS.items():
        ap.add_argument('--' + k.replace('_', '-'), type=type(v), default=v)
    a = vars(ap.parse_args())
    part = Path(a.pop('part'))
    out = a.pop('out') or part.with_name(part.stem + '_pin_support')
    run(part, out, a.pop('keep_out'), a.pop('title'), **a)


if __name__ == '__main__':
    main()
