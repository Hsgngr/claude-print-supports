"""A test part with the overhangs these tools are for: a hollow box standing open side down (its roof is a big
hidden ceiling, as in a printed shell) with a shelf sticking out of one side.

    python examples/make_example.py     # writes examples/hollow_box.stl, already in its print orientation
"""
from pathlib import Path

import manifold3d as mf

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pin_support import from_manifold  # noqa: E402

W, D, H, WALL = 70.0, 50.0, 40.0, 2.4


def box(x0, y0, z0, x1, y1, z1):
    return mf.Manifold.cube((x1 - x0, y1 - y0, z1 - z0)).translate((x0, y0, z0))


part = (box(-W / 2, -D / 2, 0, W / 2, D / 2, H) - box(-W / 2 + WALL, -D / 2 + WALL, -1, W / 2 - WALL, D / 2 - WALL, H - WALL)
        + box(W / 2, -12, 22, W / 2 + 18, 12, 25))          # the shelf: 18 mm out, 22 mm over the bed
out = Path(__file__).with_name('hollow_box.stl')
from_manifold(part).export(out)
print(out)
