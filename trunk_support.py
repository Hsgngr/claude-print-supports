"""Trunk support for any part in its print orientation: pin_support's points, but neighbouring pins share one post.

    python trunk_support.py PART.stl [--keep-out X0 Y0 X1 Y1]... [--out DIR] [--reach 14]

The pin cage prints well but slowly on a big part: every pin and brace is a separate little island on every layer,
each a stop, a retract and a tiny slow loop. On shared trunks there are far fewer islands, so it prints faster.
Trunks pay off where the slicer's own support is heavy (a big hidden ceiling); on a part where it is already light
they can lose. The points are pin_support's, unchanged (same spots, same TIP_GAP under the ceiling), but:
- groups of pins within REACH mm share one post from the bed, TRUNK_D there and TRUNK_TOP_D at its top;
- from the post a branch climbs to each pin at ANGLE deg from horizontal or steeper (steeper than an overhang, it
  prints without bridging), BR_D1 thick at the post, COL_D at the pin;
- the last STUB mm under each point are the pin's own upright column and cone;
- no bars: a post is stiff enough alone. A pin no post can reach keeps its own column, SINGLE_D thick where it fits.
Posts are picked greedily (the spot that reaches most pins first), then each pin goes to the post that gives it
the shortest branch. Everything is kept CLEAR of the part: first on a height map of the part seen from the bed
(the lowest surface over each GRID square), then for real, by growing every post, foot and branch by CLEAR and
intersecting with the part (must be empty).
Writes, next to the part: <stem>_trunks.stl (support only, same frame: load both as one multi-part object) and
<stem>_with_trunks.stl (merged, slice with supports off); in --out: build_info.json and trunks.json.
"""
import argparse
import json
from pathlib import Path

import manifold3d as mf
import numpy as np
import trimesh
from scipy import ndimage

import pin_support as ps
from pin_support import M, from_manifold

S = ps.DEFAULTS
COL_D, TIP_D, TIP_GAP, FOOT_H, CLEAR = S['col_d'], S['tip_d'], S['tip_gap'], S['foot_h'], S['clear']
TIP_CONE_H = (COL_D - TIP_D) / 2 / np.tan(np.radians(30))
TRUNK_D, TRUNK_TOP_D, TRUNK_FOOT_D = 6.0, 3.5, 10.0   # post at the bed, at its top, its foot
REACH = 14.0                                   # post to pin, in plan
ANGLE = 55.0                                   # branch slope from horizontal, as the pin cage's bars
STUB = 4.0                                     # upright pin column left under each point's cone
BR_D1, BR_TAPER = 3.2, 0.06                    # branch at the post: COL_D + BR_TAPER per mm of length, up to BR_D1
SINGLE_D, SINGLE_FOOT_D = 4.0, 8.0
GRID = 0.5
CAND = 2.0                                     # grid of places tried for a post
Z_MIN = FOOT_H + (TRUNK_FOOT_D - TRUNK_D) / 2 + 2.0   # lowest a branch may leave its post
TAN = np.tan(np.radians(ANGLE))
cone = ps.cone


class Ceiling:
    """Lowest part surface over each GRID square, seen from under the bed: 0 where it stands, inf under open sky."""

    def __init__(self, t, radii):
        lo, hi = t.bounds
        self.x0, self.y0 = lo[0] - REACH - 0.137, lo[1] - REACH - 0.137      # off the mesh's own grid lines
        xs = np.arange(self.x0, hi[0] + REACH, GRID)
        ys = np.arange(self.y0, hi[1] + REACH, GRID)
        h, _ = ps.up_hits(t, np.array([(x, y) for x in xs for y in ys]), z0=-1.0)
        h = np.maximum(h.reshape(len(xs), len(ys)), 0.0)
        self.radii = np.array(sorted(radii))
        self.maps = []
        for r in self.radii:                    # lowest surface within r of each square
            k = int(np.ceil(r / GRID))
            u = np.arange(-k, k + 1) * GRID
            self.maps.append(ndimage.minimum_filter(h, footprint=np.hypot(*np.meshgrid(u, u)) <= r + GRID / 2,
                                                    mode='constant', cval=np.inf))
        self.maps = np.array(self.maps)

    def low(self, xy, r):
        """Lowest surface within r of each xy (r: one radius or one per point, rounded up to a stored one)."""
        xy = np.atleast_2d(xy)
        i = np.clip(np.rint((xy[:, 0] - self.x0) / GRID).astype(int), 0, self.maps.shape[1] - 1)
        j = np.clip(np.rint((xy[:, 1] - self.y0) / GRID).astype(int), 0, self.maps.shape[2] - 1)
        k = np.minimum(np.searchsorted(self.radii, np.broadcast_to(r, len(xy)) - 1e-9), len(self.radii) - 1)
        return self.maps[k, i, j]


def branch_ends(c, zs, x, y, top):
    """Branch axis from the post (c, zs) to the foot of the pin's stub, and its radius at both ends."""
    a, b = np.array([c[0], c[1], zs]), np.array([x, y, top - TIP_CONE_H - STUB])
    return a, b, min(BR_D1, COL_D + BR_TAPER * np.linalg.norm(b - a)) / 2, COL_D / 2


def reach(ceil, c, pins):
    """Pins a post at c can carry: index -> (height the branch leaves the post, branch length)."""
    cap = ceil.low(c, TRUNK_D / 2 + CLEAR)[0] - CLEAR - 0.05
    if ceil.low(c, TRUNK_FOOT_D / 2 + CLEAR)[0] < Z_MIN or cap < Z_MIN:
        return {}
    out = {}
    for i, (x, y, top) in enumerate(pins):
        d = np.hypot(x - c[0], y - c[1])
        if d > REACH:
            continue
        zs = min(cap, top - TIP_CONE_H - STUB - d * TAN)
        if zs < Z_MIN:
            continue
        a, b, r0, r1 = branch_ends(c, zs, x, y, top)
        s = np.linspace(0, 1, max(2, int(np.linalg.norm(b - a) / GRID) + 1))
        q, rho = a + s[:, None] * (b - a), r0 + s * (r1 - r0) + CLEAR
        if np.all(q[:, 2] + rho < ceil.low(q[:, :2], rho)):       # a ball of the branch's size plus CLEAR fits all the way
            out[i] = (zs, float(np.linalg.norm(b - a)))
    return out


def choose(ceil, pins):
    """Posts and the pins on each: {post xy: {pin: (zs, length)}}, and the pins left on their own."""
    xy = np.array([(x, y) for x, y, _ in pins])
    lo, hi = xy.min(0) - REACH, xy.max(0) + REACH
    cands = [(x, y) for x in np.arange(lo[0], hi[0], CAND) for y in np.arange(lo[1], hi[1], CAND)
             if np.hypot(xy[:, 0] - x, xy[:, 1] - y).min() <= REACH]
    can = [reach(ceil, c, pins) for c in cands]
    left, posts = set(range(len(pins))), []
    while True:
        k = max(range(len(cands)), key=lambda j: (len(can[j].keys() & left), -sum(can[j][i][1] for i in can[j].keys() & left)))
        if len(can[k].keys() & left) < 2:
            break
        posts.append(k)
        left -= can[k].keys()
    while True:                                   # every pin to the post with its shortest branch; no post for one pin
        groups = {k: {} for k in posts}
        for i in set(range(len(pins))) - left:
            k = min((k for k in posts if i in can[k]), key=lambda k: can[k][i][1])
            groups[k][i] = can[k][i]
        thin = [k for k in posts if len(groups[k]) < 2]
        if not thin:
            break
        posts = [k for k in posts if k not in thin]
        left |= {i for i in range(len(pins)) if not any(i in can[k] for k in posts)}
    return {cands[k]: g for k, g in groups.items()}, sorted(left)


def ball(c, r):
    return mf.Manifold.sphere(r, 24).translate(tuple(c))


def post(c, zt, grow=0.0):
    f, d = TRUNK_FOOT_D + 2 * grow, TRUNK_D + 2 * grow
    return (cone(0, f, FOOT_H, f, *c) + cone(FOOT_H, f, FOOT_H + (TRUNK_FOOT_D - TRUNK_D) / 2, d, *c)
            + cone(FOOT_H, d, zt + grow, TRUNK_TOP_D + 2 * grow, *c))


def branch(c, zs, x, y, top, grow=0.0):
    a, b, r0, r1 = branch_ends(c, zs, x, y, top)
    return mf.Manifold.batch_hull([ball(a, r0 + grow), ball(b, r1 + grow)])


def point(x, y, top):
    """The pin's own last STUB mm: upright column and the cone to the TIP_D point."""
    return (cone(top - TIP_CONE_H - STUB, COL_D, top - TIP_CONE_H, COL_D, x, y)
            + cone(top - TIP_CONE_H, COL_D, top, TIP_D, x, y))


def single(ceil, pins_tool, x, y, top, foot, grow=0.0):
    """A pin on its own column, SINGLE_D thick where that clears the part, else pin_support's own pin.
    Returns (solid, thick?)."""
    z1 = top - TIP_CONE_H - STUB
    z0 = z1 - (SINGLE_D - COL_D)
    flare = FOOT_H + (SINGLE_FOOT_D - SINGLE_D) / 2
    if (z0 < flare + 0.5 or ceil.low((x, y), SINGLE_D / 2 + CLEAR)[0] - CLEAR < z0
            or ceil.low((x, y), SINGLE_FOOT_D / 2 + CLEAR)[0] < Z_MIN):   # too low or no room: the plain pin
        if not grow:
            return pins_tool.pin(x, y, top, foot), False
        zc = top - TIP_CONE_H - 1.0                                      # as pin_support's own column check
        return (cone(0, COL_D + 2 * grow, zc, COL_D + 2 * grow, x, y) if zc > 0 else mf.Manifold()), False
    f, d = SINGLE_FOOT_D + 2 * grow, SINGLE_D + 2 * grow
    col = (cone(0, f, FOOT_H, f, x, y) + cone(FOOT_H, f, FOOT_H + (SINGLE_FOOT_D - SINGLE_D) / 2, d, x, y)
           + cone(FOOT_H, d, z0, d, x, y) + cone(z0, d, z1, COL_D + 2 * grow, x, y))
    return (col if grow else col + point(x, y, top)), True


def build(ceil, pins_tool, pins, feet, groups, alone, grow=0.0):
    """The support (grow 0), or everything below the pins' stubs grown by `grow` for the clearance check."""
    parts = []
    for c, g in groups.items():
        parts.append(post(c, max(zs for zs, _ in g.values()), grow))
        for i, (zs, _) in g.items():
            parts.append(branch(c, zs, *pins[i], grow))
            if not grow:
                parts.append(point(*pins[i]))
    parts += [single(ceil, pins_tool, *pins[i], feet[i], grow)[0] for i in alone]
    return mf.Manifold.batch_boolean(parts, mf.OpType.Add)


def run(part, out, keep_out=(), title=None):
    part, out = Path(part), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    t = trimesh.load(part, force='mesh')
    if abs(t.bounds[0][2]) > 1e-3:
        raise SystemExit(f'{part.name}: bed at z {t.bounds[0][2]:.3f}, expected 0 (export it in its print orientation)')
    if not t.is_watertight:
        raise SystemExit(f'{part.name}: not watertight; the clearance check needs a closed part')
    tool = ps.Pins(t, keep_out)
    planned, cover = tool.plan()
    pins = [(x, y, z) for x, y, z, _ in planned]
    feet = [f for *_, f in planned]
    ceil = Ceiling(t, [COL_D / 2 + CLEAR, 2.0, 2.3, BR_D1 / 2 + CLEAR, TRUNK_D / 2 + CLEAR,
                       SINGLE_FOOT_D / 2 + CLEAR, TRUNK_FOOT_D / 2 + CLEAR])
    groups, alone = choose(ceil, pins)
    sup = build(ceil, tool, pins, feet, groups, alone)
    shell = M(t)
    into = (sup ^ shell).volume()
    near = (build(ceil, tool, pins, feet, groups, alone, CLEAR) ^ shell).volume()
    stem = part.with_suffix('')
    from_manifold(sup).export(f'{stem}_trunks.stl')
    from_manifold(shell + sup).export(f'{stem}_with_trunks.stl')
    reload = {k: trimesh.load(f'{stem}{k}.stl', force='mesh') for k in ('_trunks', '_with_trunks')}
    lengths = [l for g in groups.values() for _, l in g.values()]
    slopes = [np.degrees(np.arctan2(pins[i][2] - TIP_CONE_H - STUB - zs, d)) if d > 1e-6 else 90.0   # a pin right over its post
              for c, g in groups.items() for i, (zs, _) in g.items()
              for d in [np.hypot(pins[i][0] - c[0], pins[i][1] - c[1])]]
    info = dict(part=part.name, title=title or part.stem, pins=len(pins), posts=len(groups), pins_on_posts=len(lengths),
                pins_alone=len(alone), pins_alone_thick=sum(single(ceil, tool, *pins[i], feet[i])[1] for i in alone),
                pins_per_post=sorted((len(g) for g in groups.values()), reverse=True), **cover,
                tallest_post_mm=round(max((max(zs for zs, _ in g.values()) for g in groups.values()), default=0), 1),
                longest_branch_mm=round(max(lengths, default=0), 1),
                mean_branch_mm=round(float(np.mean(lengths)), 1) if lengths else 0,
                flattest_branch_deg=round(min(slopes, default=90), 1),
                support_cm3=round(sup.volume() / 1000, 1), support_g_petg=round(sup.volume() * 1.27e-3, 1),
                support_into_part_mm3=round(into, 3), grown_by_clear_into_part_mm3=round(near, 3),
                tip_gap_mm=TIP_GAP, pieces_support=len(sup.decompose()), keep_out=tool.keep_out,
                reload_trunks_watertight=bool(reload['_trunks'].is_watertight),
                reload_with_trunks_watertight=bool(reload['_with_trunks'].is_watertight),
                settings=dict(trunk_d=[TRUNK_D, TRUNK_TOP_D], trunk_foot_d=TRUNK_FOOT_D, reach_mm=REACH, branch_angle_deg=ANGLE,
                              stub_mm=STUB, branch_d=[COL_D, BR_D1], single_d=SINGLE_D, clear_mm=CLEAR, tip_d=TIP_D,
                              pins=tool.s))
    (out / 'build_info.json').write_text(json.dumps(info, indent=1))
    (out / 'trunks.json').write_text(json.dumps(dict(
        posts=[dict(xy=[round(float(v), 2) for v in c], top=round(max(zs for zs, _ in g.values()), 2),
                    pins={int(i): round(zs, 2) for i, (zs, _) in g.items()}) for c, g in groups.items()],
        alone=[int(i) for i in alone], pins=[[round(float(v), 3) for v in q] for q in pins]), indent=1))
    print(json.dumps({k: v for k, v in info.items() if k != 'settings'}, indent=1))
    return info


def main():
    global REACH
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('part')
    ap.add_argument('--out', help='folder for build_info.json and trunks.json (default <stem>_trunk_support next to the part)')
    ap.add_argument('--keep-out', nargs=4, type=float, action='append', default=[], metavar=('X0', 'Y0', 'X1', 'Y1'))
    ap.add_argument('--title')
    ap.add_argument('--reach', type=float, default=REACH)
    a = ap.parse_args()
    REACH = a.reach
    part = Path(a.part)
    out = Path(a.out) if a.out else part.with_name(part.stem + '_trunk_support')
    run(part, out, a.keep_out, a.title)


if __name__ == '__main__':
    main()
