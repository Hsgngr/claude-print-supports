"""A test part like a printed shell: a hollow dome standing open side down. Its ceiling curves, so the slicer can't
bridge it the way it bridges the flat roof of hollow_box.stl, and has to grow support inside.

    python examples/make_dome.py     # writes examples/hollow_dome.stl, already in its print orientation
"""
from pathlib import Path

import manifold3d as mf

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pin_support import from_manifold  # noqa: E402

RX, RY, RZ, WALL = 45.0, 35.0, 45.0, 2.4


def ellipsoid(rx, ry, rz):
    return mf.Manifold.sphere(1.0, 160).scale((rx, ry, rz))


# the shell, then cut 1 mm above its equator: one clean cut through both walls (cutting each half at its own
# equator leaves a seam the two cuts share)
shell = (ellipsoid(RX, RY, RZ) - ellipsoid(RX - WALL, RY - WALL, RZ - WALL)).translate((0, 0, -1.0))
part = shell ^ mf.Manifold.cube((4 * RX, 4 * RY, 2 * RZ)).translate((-2 * RX, -2 * RY, 0))
out = Path(__file__).with_name('hollow_dome.stl')
from_manifold(part).export(out)
print(out)
