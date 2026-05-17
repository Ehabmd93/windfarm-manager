"""
Generic KML / KMZ Parser — works for any project.
Parses any KML or KMZ file and returns layers as GeoJSON FeatureCollections.
"""
import xml.etree.ElementTree as ET
import json, os, io, zipfile

# Auto-assigned layer colours (cycles if more layers than colours)
LAYER_COLORS = [
    '#ef4444', '#3b82f6', '#22c55e', '#f59e0b', '#a855f7',
    '#06b6d4', '#f97316', '#84cc16', '#ec4899', '#14b8a6',
]


def _parse_coords(coord_text, is_point):
    pts = []
    for token in (coord_text or '').strip().split():
        parts = token.split(',')
        if len(parts) >= 2:
            try:
                pts.append([float(parts[0]), float(parts[1])])
            except ValueError:
                pass
    if is_point:
        return pts[0] if pts else None
    return pts if pts else None


def _parse_kml_bytes(kml_bytes):
    """Parse raw KML bytes → {layer_name: FeatureCollection}."""
    layers = {}          # layer_name → [features]
    folder_stack = []    # stack of folder name strings
    in_placemark  = False
    naming_folder = False
    pm_name = pm_coords = pm_geom_type = None

    try:
        it = ET.iterparse(io.BytesIO(kml_bytes), events=('start', 'end'))
    except Exception:
        return {}

    for event, elem in it:
        tag = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag

        if event == 'start':
            if tag == 'Folder':
                folder_stack.append('')
                naming_folder = True
            elif tag == 'Placemark':
                in_placemark  = True
                naming_folder = False
                pm_name = pm_coords = pm_geom_type = None

        elif event == 'end':
            if tag == 'name':
                text = (elem.text or '').strip()
                if naming_folder and folder_stack:
                    folder_stack[-1] = text
                    naming_folder = False
                elif in_placemark and pm_name is None:
                    pm_name = text

            elif tag == 'coordinates' and in_placemark:
                pm_coords = elem.text

            elif tag in ('Point', 'LineString', 'LinearRing', 'Polygon') and in_placemark:
                pm_geom_type = tag

            elif tag == 'Placemark' and in_placemark:
                layer_name = next(
                    (n for n in reversed(folder_stack) if n), 'Default'
                )
                if pm_coords and pm_geom_type:
                    is_pt = pm_geom_type == 'Point'
                    coords = _parse_coords(pm_coords, is_pt)
                    if coords:
                        if is_pt:
                            geom = {'type': 'Point', 'coordinates': coords}
                        elif pm_geom_type in ('Polygon', 'LinearRing'):
                            geom = {'type': 'Polygon', 'coordinates': [coords]}
                        else:
                            geom = {'type': 'LineString', 'coordinates': coords}

                        layers.setdefault(layer_name, []).append({
                            'type': 'Feature',
                            'properties': {'name': pm_name or '', 'layer': layer_name},
                            'geometry': geom,
                        })

                in_placemark  = False
                naming_folder = False
                pm_name = pm_coords = pm_geom_type = None
                elem.clear()

            elif tag == 'Folder':
                if folder_stack:
                    folder_stack.pop()

    return {
        k: {'type': 'FeatureCollection', 'features': v}
        for k, v in layers.items()
        if v
    }


def parse_bytes(file_bytes, filename):
    """
    Parse KML or KMZ from raw bytes (for uploaded files).
    Returns dict of {layer_name: FeatureCollection}.
    """
    ext = os.path.splitext(filename)[1].lower()

    if ext == '.kmz':
        try:
            with zipfile.ZipFile(io.BytesIO(file_bytes), 'r') as z:
                kml_names = [n for n in z.namelist() if n.lower().endswith('.kml')]
                if not kml_names:
                    return {}
                main = next((n for n in kml_names if 'doc.kml' in n.lower()), kml_names[0])
                kml_bytes = z.read(main)
        except Exception:
            return {}
    elif ext == '.kml':
        kml_bytes = file_bytes
    else:
        return {}

    return _parse_kml_bytes(kml_bytes)


def parse_file(file_path):
    """Parse a KML or KMZ file from disk."""
    try:
        with open(file_path, 'rb') as f:
            return parse_bytes(f.read(), file_path)
    except Exception:
        return {}


def get_layer_colors(layer_names):
    """Return {layer_name: hex_color} for a list of layer names."""
    return {name: LAYER_COLORS[i % len(LAYER_COLORS)] for i, name in enumerate(layer_names)}


# ── Legacy shim — kept for backward compat with old /api/kml/geojson route ────
_LEGACY_KML_PATH = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    '..', 'KML file', '20251024-23-011-KRWF-2d layout.kml'
))
_LEGACY_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'static', 'kml_cache.json')

def get_geojson(use_cache=True):
    """Legacy helper — only works if the KRWF KML file is present on disk."""
    if use_cache and os.path.exists(_LEGACY_CACHE):
        try:
            with open(_LEGACY_CACHE) as f:
                return json.load(f)
        except Exception:
            pass
    if not os.path.exists(_LEGACY_KML_PATH):
        return {}
    data = parse_file(_LEGACY_KML_PATH)
    try:
        os.makedirs(os.path.dirname(_LEGACY_CACHE), exist_ok=True)
        with open(_LEGACY_CACHE, 'w') as f:
            json.dump(data, f)
    except Exception:
        pass
    return data
