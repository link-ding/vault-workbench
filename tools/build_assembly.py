#!/usr/bin/env python3
"""Measure drawer and case STLs and write web/assembly-data.json for the 3D exterior view.

Development tool, run once per library version (needs numpy):
    python tools/build_assembly.py "D:\\VAULT库"            # everything
    python tools/build_assembly.py "D:\\VAULT库" --cases    # re-measure only the flip-lid cases
    python tools/build_assembly.py "D:\\VAULT库" --universal
    python tools/build_assembly.py "D:\\VAULT库" "/Boxes/"   # re-measure matching drawer files only

Every library file is exported in its print orientation at an arbitrary position.
This script finds, for each module / facade / roof, a rotation into the assembly
frame (X width, +Y front, +Z up) and a translation into a canonical local frame,
plus the anchor points the browser needs to stack parts:

    module  x centred, front face y = 0, lowest point z = 0
    facade  x centred, back face y = 0, bottom z = 0 (keyhole row at the bottom)
    roof    x centred, front face y = 0, main underside plane z = 0

Universal parts (knob, cap, label, stopper, handle) get canonical frames relative
to the anchor they attach to.
"""
import json
import re
import sys
from pathlib import Path

import numpy as np

FAMILY_WIDTH = {"S": 164.8, "M": 254.0, "L": 329.6, "XL": 418.8}
FACADE_ROT = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], float)  # Rx(-90): file +Z -> front, file +Y -> down
LATCH_ROT = np.array([[0, 0, -1], [0, 1, 0], [1, 0, 0]], float)  # case lock catch: pin axis (file Z) across the box, hook (file +X) up
CASE_FACADE = 10.0  # case facades are 10 mm thick; the lid reaches over them to their outer face


def load(path):
    n = int(np.fromfile(path, dtype="<u4", count=1, offset=80)[0])
    if 84 + 50 * n != path.stat().st_size:
        raise ValueError(f"not a binary STL: {path}")
    rec = np.dtype([("n", "<3f4"), ("v", "<9f4"), ("a", "<u2")])
    return np.fromfile(path, dtype=rec, count=n, offset=84)["v"].reshape(-1, 3, 3).astype(np.float64)


def normals(tris):
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    area = np.linalg.norm(n, axis=1) / 2
    ok = area > 1e-9
    n[ok] /= (2 * area[ok])[:, None]
    return n, area


def dominant_axes(tris):
    """Two orthogonal face-normal directions carrying the most area, plus their cross product."""
    n, area = normals(tris)
    lead = np.argmax(np.abs(n) > 0.1, axis=1)
    n = n * np.sign(n[np.arange(len(n)), lead])[:, None]  # fold opposite normals together
    keys, inv = np.unique(np.round(n, 2), axis=0, return_inverse=True)
    weight = np.bincount(inv.ravel(), area)
    axes = []
    for i in np.argsort(-weight):
        k = keys[i] / np.linalg.norm(keys[i])
        if all(abs(k @ a) < 0.05 for a in axes):
            m = n @ k > 0.9999
            d = (n[m] * area[m, None]).sum(0)
            axes.append(d / np.linalg.norm(d))
        if len(axes) == 2:
            break
    a, b = axes
    b = b - (b @ a) * a
    b /= np.linalg.norm(b)
    return [a, b, np.cross(a, b)]


def plane_area(tris, direction, offset_from_min, tol=0.05, facing=-1):
    """Area of faces facing `facing*direction` lying `offset_from_min` above the minimum along direction."""
    n, area = normals(tris)
    pos = tris.mean(1) @ direction
    rel = pos - (tris.reshape(-1, 3) @ direction).min()
    m = (facing * (n @ direction) > 0.999) & (np.abs(rel - offset_from_min) < tol)
    return area[m].sum()


def rotation(front, up):
    x = np.cross(front, up)
    return np.array([x, front, up])


def canonical(tris, rot, mode):
    v = tris.reshape(-1, 3) @ rot.T
    lo, hi = v.min(0), v.max(0)
    t = np.array([-(lo[0] + hi[0]) / 2, -hi[1], -lo[2]])
    if mode == "back":
        t[1] = -lo[1]
    m = np.eye(4)
    m[:3, :3] = rot
    m[:3, 3] = t
    return m, (v + t).reshape(-1, 3, 3)


def occupancy(tris2d, bounds, res):
    """Rasterise 2D triangles (N,3,2) into a boolean grid by area-proportional sampling."""
    (x0, y0), (x1, y1) = bounds
    w, h = int((x1 - x0) / res) + 1, int((y1 - y0) / res) + 1
    grid = np.zeros((h, w), bool)
    e1, e2 = tris2d[:, 1] - tris2d[:, 0], tris2d[:, 2] - tris2d[:, 0]
    a = np.abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]) / 2
    count = np.minimum(np.ceil(a / res ** 2 * 3).astype(int) + 3, 4000)
    rng = np.random.default_rng(7)
    for start in range(0, len(tris2d), 200000):
        t, c = tris2d[start:start + 200000], count[start:start + 200000]
        idx = np.repeat(np.arange(len(t)), c)
        r = rng.random((len(idx), 2))
        flip = r.sum(1) > 1
        r[flip] = 1 - r[flip]
        p = t[idx, 0] + r[:, :1] * (t[idx, 1] - t[idx, 0]) + r[:, 1:] * (t[idx, 2] - t[idx, 0])
        p = np.concatenate([p, t.reshape(-1, 2)])
        ix = np.clip(((p[:, 0] - x0) / res).astype(int), 0, w - 1)
        iy = np.clip(((p[:, 1] - y0) / res).astype(int), 0, h - 1)
        grid[iy, ix] = True
    return grid


def holes(grid):
    """Connected empty regions that do not touch the border, as (row, col) index arrays."""
    from scipy import ndimage
    lab, count = ndimage.label(~grid)
    edge = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])))
    return [np.argwhere(lab == i) for i in range(1, count + 1) if i not in edge]


def module_entry(path, family, floors):
    tris = load(path)
    axes = dominant_axes(tris)
    ext = [np.ptp(tris.reshape(-1, 3) @ a) for a in axes]
    height = 48 * floors + 9.01
    up = axes[int(np.argmin([abs(e - height) for e in ext]))]
    rest = [a for a in axes if a is not up]
    width = rest[int(np.argmin([abs(np.ptp(tris.reshape(-1, 3) @ a) - FAMILY_WIDTH[family]) for a in rest]))]
    front = next(a for a in rest if a is not width)
    # bottom: large downward plane 4.8 above the lowest point; back: closed wall at the far end
    if plane_area(tris, up, 4.8) < plane_area(tris, -up, 4.8):
        up = -up
    if plane_area(tris, front, 0.0, tol=0.5) < plane_area(tris, -front, 0.0, tol=0.5):
        front = -front
    rot = rotation(front, up)
    m, local = canonical(tris, rot, "front")
    v = local.reshape(-1, 3)
    n, area = normals(local)
    z = local.mean(1)[:, 2]
    down = (n[:, 2] < -0.999)
    up_faces = (n[:, 2] > 0.999)
    body_bottom = float(np.round(z[down & (np.abs(z - 4.8) < 0.1)].mean(), 2))
    tops = z[up_faces & (z > v[:, 2].max() - 6)]
    body_top = float(np.round(np.bincount(np.round(tops * 10).astype(int), area[up_faces & (z > v[:, 2].max() - 6)]).argmax() / 10, 2))
    half = v[:, 0].max()
    # side stopper slots: holes through the +X wall
    wall = local[(local[:, :, 0] > half - 3.5).all(1)]
    yz = wall[:, :, 1:]
    lo, hi = v[:, 1:].min(0), v[:, 1:].max(0)
    res = 0.25
    grid = occupancy(yz, (lo, hi), res)
    slots = []
    for cells in holes(grid):
        r, c = cells[:, 0], cells[:, 1]
        ly, lz = np.ptp(c) * res + res, np.ptp(r) * res + res
        if 12 < ly < 25 and 5 < lz < 12:
            slots.append({"y": round(lo[0] + c.mean() * res + res / 2, 2), "z": round(lo[1] + r.mean() * res + res / 2, 2),
                          "length": round(ly, 2), "height": round(lz, 2)})
    slots.sort(key=lambda s: s["z"])
    return {"role": "module", "matrix": m.ravel().round(5).tolist(), "width": round(2 * half, 2),
            "depth": round(-v[:, 1].min(), 2), "height": round(v[:, 2].max(), 2),
            "bodyBottom": body_bottom, "bodyTop": body_top, "slots": slots}


def interlock_front(tris, up, front):
    """Orient `front` so the underside interlock feature lies 24.56 mm behind the front edge."""
    v = tris.reshape(-1, 3)
    n, area = normals(tris)
    c = tris.mean(1)
    feat = (n @ up < -0.999) & (np.abs(c @ up - (v @ up).min()) < 0.05)
    fy = (c[feat] * area[feat, None]).sum(0) / area[feat].sum() @ front
    if abs((v @ front).max() - fy - 24.56) > abs(fy - (v @ front).min() - 24.56):
        return -front
    return front


def clip_back_distance(local):
    """Distance from the back edge to the centroid of the stacking clip tops (canonical frame)."""
    n, area = normals(local)
    c = local.mean(1)
    s = (n[:, 2] > 0.999) & (c[:, 2] > local[:, :, 2].max() - 0.3)
    return np.ptp(local[:, :, 1]) + (c[s, 1] * area[s]).sum() / area[s].sum()


def roof_entry(path, family):
    tris = load(path)
    axes = dominant_axes(tris)
    v = tris.reshape(-1, 3)
    ext = [np.ptp(v @ a) for a in axes]
    up = axes[int(np.argmin(ext))]
    rest = [a for a in axes if a is not up]
    width = rest[int(np.argmin([abs(np.ptp(v @ a) - FAMILY_WIDTH[family]) for a in rest]))]
    front = next(a for a in rest if a is not width)
    if plane_area(tris, up, 4.8) < plane_area(tris, -up, 4.8):
        up = -up
    front = interlock_front(tris, up, front)
    rot = rotation(front, up)
    m, local = canonical(tris, rot, "front")
    m[2, 3] -= 4.8
    local[:, :, 2] -= 4.8
    lv = local.reshape(-1, 3)
    entry = {"role": "roof", "matrix": m.ravel().round(5).tolist(), "width": round(np.ptp(lv[:, 0]), 2),
             "depth": round(-lv[:, 1].min(), 2), "top": round(lv[:, 2].max(), 2)}
    if "Grid Roof" in path.name:
        n, area = normals(local)
        zc = local.mean(1)[:, 2]
        top = lv[:, 2].max()
        rib = local[(n[:, 2] > 0.999) & (np.abs(zc - top) < 0.05)]
        res = 0.25
        lo, hi = lv[:, :2].min(0), lv[:, :2].max(0)
        grid = occupancy(rib[:, :, :2], (lo, hi), res)
        col = grid[:, int((0 - lo[0]) / res) - 8:int((0 - lo[0]) / res) + 8].any(1)
        runs, start = [], None
        for i, filled in enumerate(col):
            if not filled and start is None:
                start = i
            if filled and start is not None:
                runs.append((start, i))
                start = None
        cells = [(lo[1] + a * res, lo[1] + b * res) for a, b in runs if 30 < (b - a) * res < 45]
        centre = -entry["depth"] / 2
        row = min(cells, key=lambda r: abs((r[0] + r[1]) / 2 - centre))
        floor = local[(n[:, 2] > 0.999) & (np.abs(local.mean(1)[:, 1] - (row[0] + row[1]) / 2) < 10)
                      & (np.abs(local.mean(1)[:, 0]) < 15)].mean(1)[:, 2]
        entry["handle"] = {"y": round((row[0] + row[1]) / 2, 2), "z": round(float(np.median(floor)), 2),
                           "rows": [[round(a, 2), round(b, 2)] for a, b in cells]}
    if "Heawyweight" in path.name:
        # the carry bar rests at the back end of the slot through the inner cup walls
        wall = local[((local[:, :, 0] > 58) & (local[:, :, 0] < 64)).all(1)]
        lo, hi = lv[:, 1:].min(0), lv[:, 1:].max(0)
        res = 0.25
        for cells in holes(occupancy(wall[:, :, 1:], (lo, hi), res)):
            r, c = cells[:, 0], cells[:, 1]
            length, height = np.ptp(c) * res + res, np.ptp(r) * res + res
            if 40 < length < 80 and 25 < height < 35:
                yc, zc = lo[0] + c.mean() * res + res / 2, lo[1] + r.mean() * res + res / 2
                entry["bar"] = {"y": round(yc - length / 2 + height / 2, 2), "z": round(zc, 2)}
    return entry


def section(tris, axis, value):
    """Segments (N,2,3) where the plane coordinate[axis] = value cuts the triangles."""
    d = tris[:, :, axis] - value
    cut = tris[(d.min(1) < 0) & (d.max(1) > 0)]
    d = cut[:, :, axis] - value
    segments = []
    for i in range(3):
        j = (i + 1) % 3
        crossing = (d[:, i] < 0) != (d[:, j] < 0)
        s = np.where(crossing, d[:, i] / np.where(crossing, d[:, i] - d[:, j], 1), 0)
        segments.append((cut[:, i] + s[:, None] * (cut[:, j] - cut[:, i]), crossing))
    points = [np.stack([p for p, ok in segments if True], 1)[k][[ok[k] for _, ok in segments]] for k in range(len(cut))]
    return np.array([q[:2] for q in points if len(q) >= 2])


def case_module_entry(path, family):
    tris = load(path)
    axes = dominant_axes(tris)
    v = tris.reshape(-1, 3)
    up = max((sign * a for a in axes for sign in (1, -1)), key=lambda d: plane_area(tris, d, 4.8))
    rest = [a for a in axes if abs(a @ up) < 0.5]
    width = min(rest, key=lambda a: abs(np.ptp(v @ a) - FAMILY_WIDTH[family]))
    front = interlock_front(tris, up, next(a for a in rest if a is not width))
    m, local = canonical(tris, rotation(front, up), "front")
    lv = local.reshape(-1, 3)
    # rim = top of the outer side walls (dividers inside some L/XL modules stand higher)
    walls = lv[np.abs(lv[:, 0]) > lv[:, 0].max() - 3]
    return {"role": "caseModule", "matrix": m.ravel().round(5).tolist(), "width": round(np.ptp(lv[:, 0]), 2),
            "depth": round(-lv[:, 1].min(), 2), "rim": round(walls[:, 2].max(), 2)}


def case_lid_rotation(tris, family, ref_back):
    """Lid frame: stacking clips on top; front chosen so the clip pattern matches a drawer module of the same depth."""
    axes = dominant_axes(tris)
    v = tris.reshape(-1, 3)
    ext = [np.ptp(v @ a) for a in axes]
    up = axes[int(np.argmin(ext))]
    if plane_area(tris, -up, 5.21) < plane_area(tris, up, 5.21):
        up = -up
    rest = [a for a in axes if abs(a @ up) < 0.5]
    width = min(rest, key=lambda a: abs(np.ptp(v @ a) - FAMILY_WIDTH[family]))
    front = next(a for a in rest if a is not width)
    return rotation(min((front, -front), key=lambda f: abs(clip_back_distance(canonical(tris, rotation(f, up), "front")[1]) - ref_back)), up)


def case_lid_entry(path, family, ref_back, lock_xs, lock_floor):
    tris = load(path)
    m, local = canonical(tris, case_lid_rotation(tris, family, ref_back), "front")
    n, area = normals(local)
    c = local.mean(1)
    half = local[:, :, 0].max()
    down = n[:, 2] < -0.999
    # the skirt that spans wall to wall rests on the box rim
    for z in sorted(set(np.round(c[down, 2], 1))):
        sel = down & (np.abs(c[:, 2] - z) < 0.06)
        if area[sel].sum() > 500 and c[sel, 0].max() > half - 3 and c[sel, 0].min() < -half + 3:
            skirt = z
            break
    m[2, 3] -= skirt
    local[:, :, 2] -= skirt
    lv = local.reshape(-1, 3)
    # front nose that the lock hook wraps: just behind the lock pocket face, in lid coordinates
    noses = []
    for x in lock_xs:
        cut = section(local, 0, x + 0.01).reshape(-1, 3)
        noses.append(cut[(cut[:, 1] > lock_floor - CASE_FACADE - 2.3) & (cut[:, 1] < lock_floor - CASE_FACADE), 2].max())
    return {"role": "caseLid", "matrix": m.ravel().round(5).tolist(), "width": round(np.ptp(lv[:, 0]), 2),
            "depth": round(-lv[:, 1].min(), 2), "top": round(lv[:, 2].max(), 2), "nose": round(float(np.median(noses)), 2)}


def case_facade_entry(path):
    tris = load(path)
    m, local = canonical(tris, FACADE_ROT, "back")
    lv = local.reshape(-1, 3)
    thickness, height = lv[:, 1].max(), lv[:, 2].max()
    res = 0.2
    lo, hi = lv[:, [0, 2]].min(0), lv[:, [0, 2]].max(0)
    row = np.append(occupancy(local[:, :, [0, 2]], (lo, hi), res)[-int(3 / res)], True)
    locks, start = [], None
    for i, filled in enumerate(row):
        if not filled and start is None:
            start = i
        if filled and start is not None:
            if 10 < (i - start) * res < 80:
                locks.append(round(lo[0] + (start + i) / 2 * res, 2))
            start = None
    n, area = normals(local)
    c = local.mean(1)
    front = n[:, 1] > 0.999

    def main_level(sel):
        levels = np.round(c[sel, 1] * 20).astype(int)
        return (np.bincount(levels - levels.min(), area[sel]).argmax() + levels.min()) / 20

    lock_floor = main_level(front & (np.abs(c[:, 0] - locks[0]) < 20) & (c[:, 1] < thickness - 1))
    pocket = front & (c[:, 1] > lock_floor + 0.5) & (c[:, 1] < thickness - 1)
    level = main_level(pocket)
    pv = local[pocket & (np.abs(c[:, 1] - level) < 0.03)].reshape(-1, 3)
    return {"role": "caseFacade", "matrix": m.ravel().round(5).tolist(), "width": round(np.ptp(lv[:, 0]), 2),
            "height": round(height, 2), "thickness": round(thickness, 2), "locks": locks, "lockFloor": lock_floor,
            "pocket": {"xMin": round(pv[:, 0].min(), 2), "xMax": round(pv[:, 0].max(), 2), "zMin": round(pv[:, 2].min(), 2),
                       "zMax": round(pv[:, 2].max(), 2), "floor": level}}


def case_parts(root):
    """Canonical frames for the visible case lock catch and the 1F case label."""
    u = root / "02. SUSTAINERS" / "05. Universal"
    out = {}
    for key, name in (("latch1F", "LOCK OUTER PART 1F.stl"), ("latch24F", "LOCK OUTER PART 2-4F.stl")):
        tris = load(u / "Locks" / name)
        v = tris.reshape(-1, 3) @ LATCH_ROT.T
        n, area = normals(tris @ LATCH_ROT.T)
        c = (tris @ LATCH_ROT.T).mean(1)
        back = n[:, 1] < -0.999
        levels = np.round(c[back, 1] * 20).astype(int)
        weight = np.bincount(levels - levels.min(), area[back])
        # the plate's back is the rearmost of the large backward faces (frame faces inside the C are as large)
        back_y = (np.nonzero(weight > 0.5 * weight.max())[0].min() + levels.min()) / 20
        t = -np.array([(v[:, 0].min() + v[:, 0].max()) / 2, back_y, v[:, 2].max()])
        local = v + t
        # only the hook reaches more than 3 mm behind the plate; its lowest point there is the tip.
        # Above the tip and over the lid nose (up to 2.3 mm behind the plate) the lowest material is the inner ceiling.
        tip = local[local[:, 1] < -3, 2].min()
        inner = local[(local[:, 1] > -2.3) & (local[:, 1] < -0.8) & (local[:, 2] > tip)]
        m = np.eye(4)
        m[:3, :3] = LATCH_ROT
        m[:3, 3] = t
        out[key] = m.ravel().round(5).tolist()
        out[key + "Ceiling"] = round(float(inner[:, 2].min()), 2)
    label = load(u / "Label for Sustainers.stl").reshape(-1, 3)
    llo, lhi = label.min(0), label.max(0)
    m = np.eye(4)
    m[:3, :3] = FACADE_ROT
    m[:3, 3] = -FACADE_ROT @ np.array([(llo[0] + lhi[0]) / 2, llo[1], llo[2]])  # top edge (file -Y) at z = 0
    out["caseLabel"] = m.ravel().round(5).tolist()
    return out


def cases(root, report):
    """Measure every case module, facade and lid; split lids reuse the whole lid's frame."""
    base = root / "02. SUSTAINERS"
    drawer_s = sorted((root / "01. DRAWERS" / "01. S Families").glob("S[3-7]/Modules/S? Module 1[fF].stl"))
    ref_back = {}
    for path in drawer_s:
        tris = load(path)
        m = np.array(module_entry(path, "S", 1)["matrix"]).reshape(4, 4)
        ref_back[int(path.name[1])] = clip_back_distance(tris @ m[:3, :3].T + m[:3, 3])
    files, locks = {}, {}
    for path in sorted(base.rglob("*.stl")):
        fam = re.match(r"\d+\. (S|M|L|XL) Families/", path.relative_to(base).as_posix())
        if fam and "Facade" in path.name:
            entry = case_facade_entry(path)
            locks[fam[1]] = (entry["locks"], entry["lockFloor"])
            files[path.relative_to(root).as_posix()] = entry
    wholes = {}
    for path in sorted(base.rglob("*.stl")):
        rel = path.relative_to(root).as_posix()
        fam = re.match(r"\d+\. (S|M|L|XL) Families/(?:S|M|L|XL)(\d)/", path.relative_to(base).as_posix())
        if not fam:
            continue
        if "/Modules/" in rel:
            files[rel] = case_module_entry(path, fam[1])
        elif re.fullmatch(r"(?:S|M|L|XL)\d\s+Roof( Whole)?\.stl", path.name):
            files[rel] = case_lid_entry(path, fam[1], ref_back[int(fam[2])], *locks[fam[1]])
            wholes[path.parent] = (path, files[rel])
    for folder, (whole_path, whole) in wholes.items():
        parts = sorted(folder.glob("* Roof Part [12].stl"))
        if not parts:
            continue
        lo = np.min([load(p).reshape(-1, 3).min(0) for p in parts], axis=0)
        offset = load(whole_path).reshape(-1, 3).min(0) - lo
        m = np.array(whole["matrix"]).reshape(4, 4) @ np.vstack([np.hstack([np.eye(3), offset[:, None]]), [0, 0, 0, 1]])
        for p in parts:
            files[p.relative_to(root).as_posix()] = {**whole, "role": "caseLidPart", "matrix": m.ravel().round(5).tolist()}
    for rel, entry in files.items():
        entry["size"] = (root / rel).stat().st_size
        report(rel, entry)
    return files


def box_entry(path):
    """Inner drawer box: stored upright with its front at +Y. Its lowest front screw-hole row meets the facade's keyhole row."""
    tris = load(path)
    m, local = canonical(tris, np.eye(3), "front")
    lv = local.reshape(-1, 3)
    wall = local[(local[:, :, 1] > -4).all(1)]
    lo, hi = lv[:, [0, 2]].min(0), lv[:, [0, 2]].max(0)
    res = 0.2
    rows = []
    for cells in holes(occupancy(wall[:, :, [0, 2]], (lo, hi), res)):
        r, c = cells[:, 0], cells[:, 1]
        if 3 < max(np.ptp(r), np.ptp(c)) * res + res < 8 and len(r) > 150:
            rows.append(lo[1] + r.mean() * res + res / 2)
    return {"role": "box", "matrix": m.ravel().round(5).tolist(), "width": round(np.ptp(lv[:, 0]), 2),
            "depth": round(-lv[:, 1].min(), 2), "height": round(lv[:, 2].max(), 2), "holeZ": round(min(rows), 2)}


def end_area(tris, axis, end):
    """Area of faces looking outward at one end (+1 max, -1 min) along axis."""
    n, area = normals(tris)
    pos = tris.mean(1) @ axis
    lim = (tris.reshape(-1, 3) @ axis).max() if end > 0 else (tris.reshape(-1, 3) @ axis).min()
    return area[(end * (n @ axis) > 0.999) & (np.abs(pos - lim) < 0.6)].sum()


def end_holes(tris, axis, end):
    """Screw-sized holes through the wall at one end along axis."""
    v = tris.reshape(-1, 3)
    lim = (v @ axis).max() if end > 0 else (v @ axis).min()
    slab = tris[(np.abs(tris @ axis - lim) < 4).all(1)]
    u = np.cross(axis, [0, 0, 1.0]) if abs(axis[2]) < 0.9 else np.array([1.0, 0, 0])
    u /= np.linalg.norm(u)
    w = np.cross(axis, u)
    flat = np.stack([v @ u, v @ w], -1)
    grid = occupancy(np.stack([slab @ u, slab @ w], -1), (flat.min(0), flat.max(0)), 0.2)
    return sum(1 for c in holes(grid) if 3 < max(np.ptp(c[:, 0]), np.ptp(c[:, 1])) * 0.2 + 0.2 < 8 and len(c) > 150)


def xl_box_rotation(tris):
    """XL boxes are printed standing on one end. Bottom: the large flat end of the shortest axis.
    Front: the end wall with screw holes; a part without them (the rear half) faces its open joint forward."""
    axes = dominant_axes(tris)
    v = tris.reshape(-1, 3)
    ext = [np.ptp(v @ a) for a in axes]
    height = axes[int(np.argmin(ext))]
    up = -height if end_area(tris, height, 1) > end_area(tris, height, -1) else height
    width = axes[int(np.argmax(ext))]
    depth = next(a for a in axes if a is not height and a is not width)
    holes_at = {end: end_holes(tris, depth, end) for end in (1, -1)}
    if any(holes_at.values()):
        front = depth if holes_at[1] > holes_at[-1] else -depth
    else:
        front = depth if end_area(tris, depth, 1) < end_area(tris, depth, -1) else -depth
    return rotation(front, up)


def xl_box_entry(path):
    tris = load(path)
    m, local = canonical(tris, xl_box_rotation(tris), "front")
    entry = box_measure(m, local)
    entry["part"] = int(re.search(r"Part (\d)", path.name)[1]) if "Part" in path.name else 0
    return entry


def box_measure(m, local):
    lv = local.reshape(-1, 3)
    wall = local[(local[:, :, 1] > -4).all(1)]
    lo, hi = lv[:, [0, 2]].min(0), lv[:, [0, 2]].max(0)
    res = 0.2
    rows = []
    for cells in holes(occupancy(wall[:, :, [0, 2]], (lo, hi), res)):
        r, c = cells[:, 0], cells[:, 1]
        if 3 < max(np.ptp(r), np.ptp(c)) * res + res < 8 and len(r) > 150:
            rows.append(lo[1] + r.mean() * res + res / 2)
    # inner floor: the largest upward plane in the lowest 6 mm
    n, area = normals(local)
    c = local.mean(1)
    up = (n[:, 2] > 0.999) & (c[:, 2] < 6)
    levels = np.round(c[up, 2] * 20).astype(int)
    floor = (np.bincount(levels - levels.min(), area[up]).argmax() + levels.min()) / 20
    return {"role": "box", "matrix": m.ravel().round(5).tolist(), "width": round(np.ptp(lv[:, 0]), 2),
            "depth": round(-lv[:, 1].min(), 2), "height": round(lv[:, 2].max(), 2),
            "holeZ": round(min(rows), 2) if rows else None, "floor": floor}


def back_panel_entry(path):
    """XL back panel: stored at the same angle as its box, so the box's rotation applies; front face at y = 0."""
    box = next(path.parent.glob("* Box 1F.stl"))
    m, local = canonical(load(path), xl_box_rotation(load(box)), "front")
    lv = local.reshape(-1, 3)
    return {"role": "backPanel", "matrix": m.ravel().round(5).tolist(), "thickness": round(-lv[:, 1].min(), 2),
            "height": round(lv[:, 2].max(), 2)}


def xl_baseplate_entry(path):
    """XL baseplate: stored like the boxes; it rests on the box floor behind the front wall, as S/M/L plates do."""
    tris = load(path)
    axes = dominant_axes(tris)
    v = tris.reshape(-1, 3)
    ext = [np.ptp(v @ a) for a in axes]
    height = axes[int(np.argmin(ext))]
    up = -height if end_area(tris, height, 1) > end_area(tris, height, -1) else height
    depth = max((a for a in axes if a is not height), key=lambda a: abs(a[2]))  # printed with depth along file Z
    front = -depth if depth[2] > 0 else depth
    m, local = canonical(tris, rotation(front, up), "front")
    folder = path.parent.parent / "Boxes"
    box = xl_box_entry(next(folder.glob("* Box 1F*.stl")))
    return {"role": "baseplate", "matrix": m.ravel().round(5).tolist(), "depth": round(-local[:, :, 1].min(), 2),
            "part": int(re.search(r"Part (\d)", path.name)[1]) if "Part" in path.name else 0,
            "frontGap": 6.6, "floor": box["floor"]}


def baseplate_entry(path):
    """Gridfinity baseplate: stored inside the model's 1F box, so its offset from that box places it."""
    tris = load(path)
    m, local = canonical(tris, np.eye(3), "front")
    box = next(path.parent.parent.glob("Boxes/* Box 1[fF].stl"))
    bv, pv = load(box).reshape(-1, 3), tris.reshape(-1, 3)
    return {"role": "baseplate", "matrix": m.ravel().round(5).tolist(),
            "frontGap": round(bv[:, 1].max() - pv[:, 1].max(), 2), "floor": round(pv[:, 2].min() - bv[:, 2].min(), 2)}


def facade_entry(path):
    tris = load(path)
    m, local = canonical(tris, FACADE_ROT, "back")
    lv = local.reshape(-1, 3)
    thickness, height = lv[:, 1].max(), lv[:, 2].max()
    res = 0.2
    lo, hi = lv[:, [0, 2]].min(0), lv[:, [0, 2]].max(0)
    grid = occupancy(local[:, :, [0, 2]], (lo, hi), res)
    found = []
    for cells in holes(grid):
        r, c = cells[:, 0], cells[:, 1]
        found.append({"x": lo[0] + c.mean() * res + res / 2, "z": lo[1] + r.mean() * res + res / 2,
                      "d": max(np.ptp(c), np.ptp(r)) * res + res})
    key = max(found, key=lambda h: h["d"])
    row = [h for h in found if 3 < h["d"] < 7 and abs(h["z"] - key["z"]) < 2]
    cap = min(row, key=lambda h: h["x"])
    # label pocket: the largest front-facing plane recessed below the outer surface beside the keyhole
    n, area = normals(local)
    c = local.mean(1)
    side = (n[:, 1] > 0.999) & (c[:, 0] > key["x"] + 20) & (np.abs(c[:, 2] - key["z"]) < 8) & (c[:, 1] < thickness - 1)
    levels = np.round(c[side, 1] * 20).astype(int)
    level = np.bincount(levels - levels.min(), area[side]).argmax() + levels.min()
    pv = local[side][levels == level].reshape(-1, 3)
    front = n[:, 1] > 0.999
    faces = np.round(c[front, 1] * 20).astype(int)
    face = (np.bincount(faces - faces.min(), area[front]).argmax() + faces.min()) / 20
    return {"role": "facade", "matrix": m.ravel().round(5).tolist(), "width": round(np.ptp(lv[:, 0]), 2),
            "height": round(height, 2), "thickness": round(thickness, 2), "face": round(face, 2),
            "keyhole": {"x": round(key["x"], 2), "z": round(key["z"], 2), "d": round(key["d"], 2)},
            "cap": {"x": round(cap["x"], 2), "z": round(cap["z"], 2)},
            "pocket": {"xMin": round(pv[:, 0].min(), 2), "xMax": round(pv[:, 0].max(), 2),
                       "z": round((pv[:, 2].min() + pv[:, 2].max()) / 2, 2), "floor": round(level / 20, 2)}}


def inside(tri, point):
    """Whether a 2D point lies inside a 2D triangle (3,2)."""
    d = [(tri[i, 0] - point[0]) * (tri[(i + 1) % 3, 1] - point[1]) - (tri[(i + 1) % 3, 0] - point[0]) * (tri[i, 1] - point[1]) for i in range(3)]
    return all(x >= 0 for x in d) or all(x <= 0 for x in d)


def universal(root):
    """Canonical frames for shared drawer parts, measured from the files themselves."""
    u = root / "01. DRAWERS" / "07. Universal"
    out = {}

    def frame(rot, t):
        m = np.eye(4)
        m[:3, :3] = rot
        m[:3, 3] = t
        return m.ravel().round(5).tolist()

    def axis_of(points):
        lo = points[:, 2].min()
        ring = points[(points[:, 2] > lo + 12) & (points[:, 2] < lo + 16)]
        return (ring[:, :2].min(0) + ring[:, :2].max(0)) / 2

    # The hidden key shares the standard key's body and ends in a flat face flush with the facade,
    # so its height marks the flush plane for both.
    hidden = load(u / "Hidden Key (Turn On Supports).stl").reshape(-1, 3)
    rot = FACADE_ROT
    hx, hy = axis_of(hidden)
    out["hiddenKey"] = frame(rot, -rot @ np.array([hx, hy, hidden[:, 2].max()]))
    key = load(u / "Key (Turn On Supports).stl").reshape(-1, 3)
    axis = axis_of(key)
    flush = key[:, 2].min() + np.ptp(hidden[:, 2])
    out["key"] = frame(rot, -rot @ np.array([axis[0], axis[1], flush]))
    # The key cap is a cup printed opening-up: its closed plate (file -Z) faces outward over the grip end.
    grip_top = key[:, 2].max()
    cap_tris = load(u / "Key Cap.stl")
    cap = cap_tris.reshape(-1, 3)
    cc = (cap.min(0) + cap.max(0)) / 2
    probe = cc[:2] + [5.01, 0.013]
    floor = sorted(t[:, 2].mean() for t in cap_tris if inside(t[:, :2], probe))[1]
    flip = rot @ np.diag([1.0, -1.0, -1.0])
    out["keyCap"] = frame(flip, np.array([0, grip_top - flush, 0]) - flip @ np.array([cc[0], cc[1], floor]))
    label = load(u / "Label.stl").reshape(-1, 3)
    llo, lhi = label.min(0), label.max(0)
    # rounded end (file -X) rests against the rounded end of the facade pocket
    out["label"] = frame(rot, -rot @ np.array([llo[0], (llo[1] + lhi[1]) / 2, llo[2]]))
    out["labelThickness"] = round(float(lhi[2] - llo[2]), 2)
    fc = load(u / "Facade Cap.stl").reshape(-1, 3)
    flo, fhi = fc.min(0), fc.max(0)
    out["facadeCap"] = frame(rot, -rot @ np.array([(flo[0] + fhi[0]) / 2, (flo[1] + fhi[1]) / 2, fhi[2]]))
    st = load(u / "Runners" / "Runner Stopper.stl").reshape(-1, 3)
    slo, shi = st.min(0), st.max(0)
    out["stopper"] = frame(np.eye(3), -np.array([shi[0], (slo[1] + shi[1]) / 2, (slo[2] + shi[2]) / 2]))
    hd = load(u / "Top Handle (Turn On Supports).stl").reshape(-1, 3)
    roty = np.array([[0, 0, 1], [0, 1, 0], [-1, 0, 0]], float)  # Ry(90): mounting plate (+X) down
    hv = hd @ roty.T
    hlo, hhi = hv.min(0), hv.max(0)
    out["topHandle"] = frame(roty, -np.array([(hlo[0] + hhi[0]) / 2, (hlo[1] + hhi[1]) / 2, hlo[2]]))
    # Carry bar along X; the insert files are stored in their assembled relation to the bar.
    rotz = np.array([[0, 1, 0], [-1, 0, 0], [0, 0, 1]], float)
    bar = load(u / "Heawyweight Roof Handle.stl").reshape(-1, 3)
    centre = (bar.min(0) + bar.max(0)) / 2
    out["heavyHandle"] = frame(rotz, -rotz @ centre)
    out["heavyInsert"] = out["heavyHandle"]
    return out


def main():
    root = Path(sys.argv[1]).expanduser().resolve()
    drawers = root / "01. DRAWERS"
    out = Path(__file__).resolve().parent.parent / "web" / "assembly-data.json"
    if sys.argv[2:] in (["--universal"], ["--cases"]):
        data = json.loads(out.read_text(encoding="utf-8"))
        if sys.argv[2] == "--cases":
            data["files"] = {k: v for k, v in data["files"].items() if not k.startswith("02. SUSTAINERS/")}
            data["files"].update(cases(root, lambda rel, e: print(rel, {k: v for k, v in e.items() if k != "matrix"}, flush=True)))
        data["universal"] = {**universal(root), **case_parts(root)}
        out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print("updated universal parts in", out)
        return
    only = sys.argv[2] if len(sys.argv) > 2 else ""
    # a path filter re-measures the matching drawer files and merges them into the existing data
    data = json.loads(out.read_text(encoding="utf-8")) if only and out.exists() else \
        {"version": 1, "frame": "X width, +Y front, +Z up, millimetres", "files": {}}
    for path in sorted(drawers.rglob("*.stl")):
        rel = path.relative_to(root).as_posix()
        if only not in rel:
            continue
        fam = re.match(r"01\. DRAWERS/\d+\. (S|M|L|XL) Families/", rel)
        if not fam:
            continue
        name = path.name
        entry = None
        if "/Modules/" in rel:
            entry = module_entry(path, fam[1], int(re.search(r"([124])f", name, re.I)[1]))
        elif "/Roofs/" in rel:
            entry = roof_entry(path, fam[1])
        elif "/Facades/" in rel or name == "XL Facade 1F.stl":
            entry = facade_entry(path)
        elif "/Boxes/" in rel and fam[1] == "XL":
            entry = back_panel_entry(path) if "Back Panel" in name else xl_box_entry(path)
        elif "/Boxes/" in rel:
            entry = box_entry(path)
        elif "/Gridfinity Baseplate/" in rel:
            entry = xl_baseplate_entry(path) if fam[1] == "XL" else baseplate_entry(path)
        if entry:
            entry["size"] = path.stat().st_size
            data["files"][rel] = entry
            print(rel, {k: v for k, v in entry.items() if k not in ("matrix",)}, flush=True)
    if not only:
        data["files"].update(cases(root, lambda rel, e: print(rel, flush=True)))
    data["universal"] = {**universal(root), **case_parts(root)}
    out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("wrote", out, len(data["files"]), "files")


if __name__ == "__main__":
    main()
