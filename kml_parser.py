"""
KML Parser — extracts key layers from the KRWF site layout KML
and returns them as GeoJSON FeatureCollections.
"""
import xml.etree.ElementTree as ET
import json, os, re

KML_PATH = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    '..', 'KML file', '20251024-23-011-KRWF-2d layout.kml'
))
CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'static', 'kml_cache.json')

# Folder name fragments → output layer key
LAYER_MAP = {
    'des wtg ctr z des': 'wtg_centers',
    'des hs str':        'hardstand',
    'des bf str':        'blade_fingers',
    'des bp str':        'boom_pad',
    'des track cl':      'track',
}

def _coords_to_list(coord_text, geom_type):
    pts = []
    for token in coord_text.strip().split():
        parts = token.split(',')
        if len(parts) >= 2:
            try:
                pts.append([float(parts[0]), float(parts[1])])
            except ValueError:
                pass
    return pts[0] if (geom_type == 'Point' and pts) else pts

def _match_layer(name):
    nl = name.lower()
    for frag, key in LAYER_MAP.items():
        if frag in nl:
            return key
    return None


def parse_kml_to_geojson():
    if not os.path.exists(KML_PATH):
        print(f"KML not found at: {KML_PATH}")
        return {}

    layers = {key: [] for key in LAYER_MAP.values()}

    # State machine
    folder_stack  = []   # stack of (folder_name, layer_key)
    in_placemark  = False
    naming_folder = False   # are we reading the Folder's own <name>?
    pm_name = pm_coords = pm_geom_type = None

    for event, elem in ET.iterparse(KML_PATH, events=('start', 'end')):
        local = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag

        if event == 'start':
            if local == 'Folder':
                folder_stack.append(('', None))  # placeholder until we read name
                naming_folder = True  # next <name> end belongs to this folder

            elif local == 'Placemark':
                in_placemark  = True
                naming_folder = False
                pm_name = pm_coords = pm_geom_type = None

        elif event == 'end':
            if local == 'name':
                text = (elem.text or '').strip()
                if naming_folder and folder_stack:
                    # Replace the last placeholder with the real name + layer
                    folder_stack[-1] = (text, _match_layer(text))
                    naming_folder = False
                elif in_placemark and pm_name is None:
                    pm_name = text

            elif local == 'coordinates' and in_placemark:
                pm_coords = elem.text

            elif local in ('Point', 'LineString') and in_placemark:
                pm_geom_type = local

            elif local == 'Placemark' and in_placemark:
                # Figure out which layer we're in
                active_layer = next(
                    (lyr for _, lyr in reversed(folder_stack) if lyr), None
                )

                if active_layer and pm_coords and pm_geom_type:
                    coords = _coords_to_list(pm_coords, pm_geom_type)
                    if coords:
                        geom = ({'type': 'Point',      'coordinates': coords}
                                if pm_geom_type == 'Point'
                                else {'type': 'LineString', 'coordinates': coords})

                        name = pm_name or ''
                        if active_layer == 'wtg_centers' and re.match(r'^\d+$', name):
                            name = f'WTG{int(name):02d}'

                        layers[active_layer].append({
                            'type': 'Feature',
                            'properties': {'name': name, 'layer': active_layer},
                            'geometry': geom
                        })

                in_placemark  = False
                naming_folder = False
                pm_name = pm_coords = pm_geom_type = None
                elem.clear()

            elif local == 'Folder':
                if folder_stack:
                    folder_stack.pop()

    result = {k: {'type':'FeatureCollection','features':v}
              for k, v in layers.items() if v}
    return result


def get_geojson(use_cache=True):
    if use_cache and os.path.exists(CACHE_PATH):
        with open(CACHE_PATH) as f:
            return json.load(f)

    print("Parsing KML (first run — caching result)…")
    data = parse_kml_to_geojson()
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, 'w') as f:
        json.dump(data, f)
    for k, v in data.items():
        print(f"  {k}: {len(v['features'])} features")
    return data


if __name__ == '__main__':
    data = get_geojson(use_cache=False)
    for layer, fc in data.items():
        print(f"  {layer}: {len(fc['features'])} features")
        if fc['features']:
            print(f"    first feature name: {fc['features'][0]['properties']['name']}")
