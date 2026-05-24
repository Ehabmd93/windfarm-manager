"""
project_config.py — Project-type profiles.

Maps each project type to:
  element_label     — singular label for an element (e.g. "WTG", "Section")
  element_plural    — plural label (e.g. "WTGs", "Sections")
  element_types     — allowed ELEMENT_TYPES keys + labels for this profile
  default_features  — features enabled by default when creating this project type
  map_placeholder   — placeholder text for the "name" field on the map pin panel
  examples          — example names shown in setup UI
  has_foundation    — whether foundation tracker makes sense for this type
  area_label        — generic word for a sub-area ("Area", "Zone", "Sub-area")
  company_label     — default label for the main contractor company field
"""

# ── Profile definitions ───────────────────────────────────────────────────────

_WIND_TYPES   = ['wtg', 'access_track', 'hardstand', 'crane_pad', 'substation', 'other']
_CIVIL_TYPES  = ['section', 'lot', 'zone', 'structure', 'road_section', 'drainage', 'other']
_GENERAL_TYPES = ['element', 'section', 'lot', 'zone', 'structure', 'other']

PROJECT_TYPE_PROFILES = {
    'Wind Farm': {
        'element_label':    'WTG',
        'element_plural':   'WTGs',
        'element_types':    _WIND_TYPES,
        'default_features': ['proof_rolling', 'geo_testing', 'itp', 'foundation',
                             'progress_tracker', 'documents'],
        'map_placeholder':  'e.g. WTG01, Hardstand',
        'examples':         ['WTG01', 'WTG02', 'Hardstand', 'Crane Pad'],
        'has_foundation':   True,
        'area_label':       'Area',
        'company_label':    'Contractor',
    },
    'Solar Farm': {
        'element_label':    'Array',
        'element_plural':   'Arrays',
        'element_types':    ['section', 'zone', 'substation', 'other'],
        'default_features': ['itp', 'progress_tracker', 'documents'],
        'map_placeholder':  'e.g. Array A1, Substation',
        'examples':         ['Array A1', 'Array B2', 'Substation', 'Access Road'],
        'has_foundation':   False,
        'area_label':       'Zone',
        'company_label':    'Contractor',
    },
    'Civil Construction': {
        'element_label':    'Element',
        'element_plural':   'Elements',
        'element_types':    _CIVIL_TYPES,
        'default_features': ['proof_rolling', 'geo_testing', 'itp', 'progress_tracker', 'documents'],
        'map_placeholder':  'e.g. Section 1, Structure A',
        'examples':         ['Section 1', 'Structure A', 'Culvert B', 'Zone 3'],
        'has_foundation':   False,
        'area_label':       'Area',
        'company_label':    'Main Contractor',
    },
    'Road Works': {
        'element_label':    'Section',
        'element_plural':   'Sections',
        'element_types':    ['road_section', 'drainage', 'section', 'zone', 'other'],
        'default_features': ['proof_rolling', 'geo_testing', 'itp', 'progress_tracker', 'documents'],
        'map_placeholder':  'e.g. Ch 0+000 – 0+500, Intersection A',
        'examples':         ['Ch 0+000–0+500', 'Ch 0+500–1+000', 'Drainage A', 'Intersection B'],
        'has_foundation':   False,
        'area_label':       'Sub-area',
        'company_label':    'Main Contractor',
    },
    'Earthworks': {
        'element_label':    'Lot',
        'element_plural':   'Lots',
        'element_types':    ['lot', 'section', 'zone', 'other'],
        'default_features': ['proof_rolling', 'geo_testing', 'itp', 'progress_tracker', 'documents'],
        'map_placeholder':  'e.g. Lot 1, Fill Zone A',
        'examples':         ['Lot 1', 'Lot 2', 'Fill Zone A', 'Embankment B'],
        'has_foundation':   False,
        'area_label':       'Zone',
        'company_label':    'Main Contractor',
    },
    'Mining': {
        'element_label':    'Area',
        'element_plural':   'Areas',
        'element_types':    ['zone', 'section', 'lot', 'structure', 'other'],
        'default_features': ['itp', 'documents', 'progress_tracker'],
        'map_placeholder':  'e.g. Pit North, Pad A',
        'examples':         ['Pit North', 'ROM Pad A', 'Workshop', 'Access Road'],
        'has_foundation':   False,
        'area_label':       'Zone',
        'company_label':    'Mine Contractor',
    },
    'Infrastructure': {
        'element_label':    'Asset',
        'element_plural':   'Assets',
        'element_types':    _CIVIL_TYPES,
        'default_features': ['itp', 'documents', 'progress_tracker'],
        'map_placeholder':  'e.g. Bridge A, Culvert 3',
        'examples':         ['Bridge A', 'Culvert 3', 'Retaining Wall B'],
        'has_foundation':   False,
        'area_label':       'Area',
        'company_label':    'Main Contractor',
    },
    'Other': {
        'element_label':    'Element',
        'element_plural':   'Elements',
        'element_types':    _GENERAL_TYPES,
        'default_features': ['itp', 'documents', 'progress_tracker'],
        'map_placeholder':  'e.g. Element 1',
        'examples':         ['Element 1', 'Element 2', 'Zone A'],
        'has_foundation':   False,
        'area_label':       'Area',
        'company_label':    'Contractor',
    },
}

# Element type labels — generic enough for non-wind projects
GENERIC_ELEMENT_TYPE_LABELS = {
    'wtg':          'Wind Turbine (WTG)',
    'access_track': 'Access Track',
    'hardstand':    'Hardstand',
    'crane_pad':    'Crane Pad',
    'substation':   'Substation',
    'section':      'Section',
    'lot':          'Lot',
    'zone':         'Zone',
    'structure':    'Structure',
    'road_section': 'Road Section',
    'drainage':     'Drainage',
    'element':      'Element',
    'other':        'Other',
}


def get_profile(project_type: str) -> dict:
    """Return the type profile dict for a given project_type string.
    Falls back to 'Other' profile if type is unknown."""
    return PROJECT_TYPE_PROFILES.get(project_type, PROJECT_TYPE_PROFILES['Other'])


def is_wind_farm(project_type: str) -> bool:
    return project_type == 'Wind Farm'


def default_features_for(project_type: str) -> list:
    return get_profile(project_type)['default_features']
