"""
CAD (DXF) Parser — converts AutoCAD DXF drawings into map-ready GeoJSON layers.

Output contract matches kml_parser.parse_bytes exactly:
    {layer_name: {'type': 'FeatureCollection', 'features': [...]}}
so uploaded CAD drawings flow through the same storage + rendering pipeline
as KML/KMZ site layouts.

Notes on formats:
- DXF is AutoCAD's open exchange format — every CAD package (AutoCAD, Civil 3D,
  BricsCAD, DraftSight, QGIS) exports it via File → Save As → DXF.
- DWG is a closed binary format with no reliable open-source parser; callers
  should instruct users to export DXF (the upload route returns a guided
  message for .dwg files).

Coordinates:
- CAD site drawings are usually in a projected CRS (in Australia: GDA2020 /
  MGA zones, EPSG 7846-7859, or GDA94 / MGA 28348-28358). pyproj transforms
  the chosen EPSG → WGS84 for the web map.
- If the drawing already looks like lon/lat (|x|<=180, |y|<=90) the transform
  is skipped automatically.

Supported entities: LINE, LWPOLYLINE, POLYLINE, CIRCLE, ARC, ELLIPSE, SPLINE,
POINT, TEXT, MTEXT. Others (hatches, dimensions, blocks) are skipped — site
layouts are lines and text.
"""
import io
import math

MAX_FEATURES = 20000       # hard cap so a huge drawing can't blow up the DB/browser
CURVE_SEGMENTS = 36        # circle/arc flattening resolution

# Common Australian projected CRS choices offered in the upload UI.
# (key, label) — key is the EPSG integer.
EPSG_CHOICES = [
    (7850, 'GDA2020 / MGA zone 50 (WA)'),
    (7851, 'GDA2020 / MGA zone 51'),
    (7852, 'GDA2020 / MGA zone 52'),
    (7853, 'GDA2020 / MGA zone 53'),
    (7854, 'GDA2020 / MGA zone 54'),
    (7855, 'GDA2020 / MGA zone 55 (VIC/NSW/TAS)'),
    (7856, 'GDA2020 / MGA zone 56 (QLD/NSW coast)'),
    (28350, 'GDA94 / MGA zone 50 (WA)'),
    (28355, 'GDA94 / MGA zone 55'),
    (28356, 'GDA94 / MGA zone 56'),
    (32750, 'WGS84 / UTM zone 50S'),
    (4326,  'Already longitude/latitude (WGS84)'),
]
DEFAULT_EPSG = 7850   # GDA2020 / MGA zone 50 — Western Australia


def _aci_to_hex(aci):
    """AutoCAD Color Index → #rrggbb (best effort; defaults to slate)."""
    try:
        from ezdxf import colors as _c
        r, g, b = _c.aci2rgb(int(aci))
        return '#{:02x}{:02x}{:02x}'.format(r, g, b)
    except Exception:
        return '#64748b'


def _looks_like_lonlat(points):
    """Heuristic: every coordinate already within lon/lat bounds."""
    for x, y in points[:50]:
        if abs(x) > 180 or abs(y) > 90:
            return False
    return bool(points)


def _make_transformer(epsg):
    """Return fn(x, y) -> (lon, lat). Identity for EPSG 4326."""
    if int(epsg) == 4326:
        return lambda x, y: (x, y)
    from pyproj import Transformer
    t = Transformer.from_crs(f'EPSG:{int(epsg)}', 'EPSG:4326', always_xy=True)
    return lambda x, y: t.transform(x, y)


def _entity_points(e):
    """Extract 2D vertex list [(x, y), ...] from a DXF entity, or None."""
    try:
        t = e.dxftype()
        if t == 'LINE':
            s, en = e.dxf.start, e.dxf.end
            return [(s.x, s.y), (en.x, en.y)], 'line'
        if t == 'LWPOLYLINE':
            pts = [(p[0], p[1]) for p in e.get_points()]
            return pts, ('polygon' if e.closed else 'line')
        if t == 'POLYLINE':
            pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
            return pts, ('polygon' if e.is_closed else 'line')
        if t == 'CIRCLE':
            c, r = e.dxf.center, e.dxf.radius
            pts = [(c.x + r * math.cos(2 * math.pi * i / CURVE_SEGMENTS),
                    c.y + r * math.sin(2 * math.pi * i / CURVE_SEGMENTS))
                   for i in range(CURVE_SEGMENTS + 1)]
            return pts, 'polygon'
        if t == 'ARC':
            c, r = e.dxf.center, e.dxf.radius
            a0 = math.radians(e.dxf.start_angle)
            a1 = math.radians(e.dxf.end_angle)
            if a1 <= a0:
                a1 += 2 * math.pi
            pts = [(c.x + r * math.cos(a0 + (a1 - a0) * i / CURVE_SEGMENTS),
                    c.y + r * math.sin(a0 + (a1 - a0) * i / CURVE_SEGMENTS))
                   for i in range(CURVE_SEGMENTS + 1)]
            return pts, 'line'
        if t in ('SPLINE', 'ELLIPSE'):
            pts = [(p.x, p.y) for p in e.flattening(0.5)]
            return pts, 'line'
        if t == 'POINT':
            p = e.dxf.location
            return [(p.x, p.y)], 'point'
        if t in ('TEXT', 'MTEXT'):
            if t == 'TEXT':
                p = e.dxf.insert
                txt = (e.dxf.text or '').strip()
            else:
                p = e.dxf.insert
                txt = (e.plain_text() or '').strip()
            e._sg_label = txt   # stash for caller
            return [(p.x, p.y)], 'point'
    except Exception:
        return None
    return None


def parse_bytes(file_bytes, filename, epsg=DEFAULT_EPSG):
    """Parse a DXF file (bytes) → {layer_name: FeatureCollection}.

    Raises ValueError with a human-readable message on unreadable files.
    """
    try:
        import ezdxf
        from ezdxf import recover
    except ImportError as exc:                        # pragma: no cover
        raise ValueError('CAD support is not installed on the server (ezdxf missing).') from exc

    try:
        doc, _auditor = recover.read(io.BytesIO(file_bytes))
    except Exception as exc:
        raise ValueError(f'Could not read DXF file: {exc}') from exc

    msp = doc.modelspace()

    # Layer colors from the DXF layer table
    layer_color = {}
    try:
        for layer in doc.layers:
            layer_color[layer.dxf.name] = _aci_to_hex(layer.color)
    except Exception:
        pass

    # First pass: collect raw entity points to run the lon/lat heuristic
    raw = []          # (layer, kind, points, label)
    sample = []
    for e in msp:
        got = _entity_points(e)
        if not got:
            continue
        pts, kind = got
        if not pts:
            continue
        label = getattr(e, '_sg_label', '')
        layer = e.dxf.layer or 'Default'
        raw.append((layer, kind, pts, label))
        sample.extend(pts[:3])
        if len(raw) >= MAX_FEATURES:
            break

    if not raw:
        return {}

    tf = (lambda x, y: (x, y)) if _looks_like_lonlat(sample) else _make_transformer(epsg)

    layers = {}
    for layer, kind, pts, label in raw:
        try:
            coords = []
            for x, y in pts:
                lon, lat = tf(x, y)
                if not (math.isfinite(lon) and math.isfinite(lat)):
                    coords = []
                    break
                coords.append([round(lon, 8), round(lat, 8)])
            if not coords:
                continue
            if kind == 'point':
                geom = {'type': 'Point', 'coordinates': coords[0]}
            elif kind == 'polygon' and len(coords) >= 3:
                if coords[0] != coords[-1]:
                    coords.append(coords[0])
                geom = {'type': 'Polygon', 'coordinates': [coords]}
            elif len(coords) >= 2:
                geom = {'type': 'LineString', 'coordinates': coords}
            else:
                continue
            layers.setdefault(layer, []).append({
                'type': 'Feature',
                'properties': {
                    'name': label or '',
                    'layer': layer,
                    'cad_color': layer_color.get(layer, '#64748b'),
                },
                'geometry': geom,
            })
        except Exception:
            continue

    return {
        k: {'type': 'FeatureCollection', 'features': v}
        for k, v in layers.items()
        if v
    }
