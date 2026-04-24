"""
ITP Definitions — King Rocks Wind Farm
Sourced directly from signed ITP documents Rev E.
"""

# Inspection type codes
INSPECTION_LEGEND = {
    'H':  'Hold Point',
    'W':  'Witness Point',
    'T':  'Test',
    'C':  'Check',
    'I':  'Inspection',
    'M':  'Monitor',
    'DR': 'Document Review',
    'SS': 'Sub Supplier Records',
    'S':  'Surveillance',
}

CLIENTS = [
    {'id': 'ritesh', 'name': 'Ritesh',  'company': 'Vestas'},
    {'id': 'mark',   'name': 'Mark',    'company': 'Vestas'},
]

ITP_DEFINITIONS = {

    # ─────────────────────────────────────────────────────────────────────────
    'ITP02': {
        'itp_number': '02',
        'name':       'ITP#2 – Access Road',
        'revision':   'E',
        'date':       '17/10/2025',
        'works':      'Access Road',
        'spec':       'KRWF-SPE-CIV-3000',
        'scope':      ('Access road around the WTG including survey set out, earthworks, '
                       'subgrade preparation and granular pavement construction.'),
        'prepared_by':  'Ehab Yousef',
        'approved_by':  'Wayne Hoey',
        'items': [
            {
                'no': '1',
                'activity': 'Survey',
                'criteria': [
                    'Ensure latest versions of plans/drawings are used for set out/modelling.',
                    'Extent of area to be marked with Pegs or incorporated into machine guidance prior to works commencing.',
                    'Monitor surface run-off, and provide silt catchments as required.',
                    'Monitor natural water basins and/or earth dams made by land owner and dredge/desilt if required.',
                ],
                'rows': [
                    {'inspection': 'DR', 'frequency': 'As required'},
                    {'inspection': 'C',  'frequency': 'Once'},
                    {'inspection': 'M',  'frequency': 'As required'},
                    {'inspection': 'M',  'frequency': 'As required'},
                ],
                'lucas_codes': ['DR', 'C', 'M'],
                'client_codes': ['W'],
                'hold_witness': None,
            },
            {
                'no': '2',
                'activity': 'Earthworks',
                'criteria': [
                    'Barricade the excavation zone as required.',
                    'Site won material can be used as cut to fill.',
                    'Grub vegetation/obstructions to ≥300 mm below stripped surface or ≥600 mm below pavement subgrade (whichever less). Backfill and compact holes same as surrounding.',
                    'All existing fill within the construction area shall be completely sorted of all deleterious material prior to replacement.',
                    'Any loose, dry surface soil remaining after compaction shall be watered to achieve the specified moisture content immediately before placing the subsequent layer.',
                    'Excavate as specified in the drawings to shape the site, aiming to reach the design subgrade.',
                    'Earthworks foundations shall be tested with one DCP per 50 lineal metres, in accordance with KRWF-SPE-CIV-3000.',
                    'General fill placed in layers ≤300 mm (loose) compacted to not less than: 92% DDR within 300 mm of finished pavement layers; 90% DDR more than 300 mm below finished pavement level.',
                    'Test frequency: 1 test within the deepest fill zone every 400 lm, every second layer within 800 mm of finished subgrade, every fourth layer greater than 800 mm below finished subgrade.',
                    'Proof roll every 3–4 layers where fill embankment >800 mm below finished subgrade, every second layer within 800 mm of finished subgrade. Record in Site Proof Roll Record Form.',
                ],
                'rows': [
                    {'inspection': 'M', 'frequency': 'Once'},
                    {'inspection': 'M', 'frequency': 'Progressively'},
                    {'inspection': 'M', 'frequency': 'Progressively'},
                    {'inspection': 'M', 'frequency': 'As required'},
                    {'inspection': 'M', 'frequency': 'As required'},
                    {'inspection': 'T', 'frequency': 'As specified'},
                    {'inspection': 'T', 'frequency': 'As specified'},
                    {'inspection': 'H', 'frequency': 'As specified'},
                ],
                'lucas_codes': ['M', 'T'],
                'client_codes': ['H'],
                'hold_witness': 'H',
            },
            {
                'no': '3',
                'activity': 'Subgrade Preparation',
                'criteria': [
                    'Fill shall be placed in continuous, approximately horizontal layers of not more than 200 mm thickness after compaction.',
                    'Excavated areas shall be finished to an even surface, trimmed to lines, levels, grades and batters shown on drawings within specified tolerances.',
                    'Subgrade layer shall be compacted to not less than 92% DDR; 1 test/400 lineal metres.',
                    'Survey pick up for the subgrade layer.',
                    'Proof roll of the subgrade, prior to commencing pavement construction.',
                ],
                'rows': [
                    {'inspection': 'M',   'frequency': 'Progressively'},
                    {'inspection': 'M',   'frequency': 'Progressively'},
                    {'inspection': 'T',   'frequency': 'As specified'},
                    {'inspection': 'M',   'frequency': 'As required'},
                    {'inspection': 'H&W', 'frequency': 'As specified'},
                ],
                'lucas_codes': ['M', 'T'],
                'client_codes': ['H', 'W'],
                'hold_witness': 'H&W',
            },
            {
                'no': '4',
                'activity': 'Granular Pavement',
                'criteria': [
                    'Pavement material — Class 3 (Austroads Part 6/4A): CBR ≥ 45%, PI = 9% ± 2%, Wet strength ≥ 50 kN. 1 test/5000 tonnes created.',
                    'Spread as a uniform homogeneous layer; after compaction minimum 150 mm thickness; maximum compacted lift 200 mm.',
                    'Compact to 99% DDR (Basecourse), 96% DDR (Subbase). Test frequency: 1 test/400 lm/layer.',
                    'Plate load tests as per Crane Pads Requirements Section A 1.12; every 1000 m and at every fill batter >5 m height.',
                    'Proof rolling of granular pavement layer at time of inspection by the Client.',
                    'Survey pick up for the finish level.',
                ],
                'rows': [
                    {'inspection': 'T',   'frequency': 'As specified'},
                    {'inspection': 'M',   'frequency': 'Progressively'},
                    {'inspection': 'T',   'frequency': 'As specified'},
                    {'inspection': 'T',   'frequency': 'As specified'},
                    {'inspection': 'H&W', 'frequency': 'As required'},
                    {'inspection': 'C',   'frequency': 'As required'},
                ],
                'lucas_codes': ['T', 'M', 'C'],
                'client_codes': ['H', 'W'],
                'hold_witness': 'H&W',
            },
            {
                'no': '5',
                'activity': 'Road Drainage',
                'criteria': [
                    'Drainage installed as per design drawings per Section B 9 of the specification.',
                ],
                'rows': [
                    {'inspection': 'M', 'frequency': 'As required'},
                ],
                'lucas_codes': ['M'],
                'client_codes': [],
                'hold_witness': None,
            },
            {
                'no': '6',
                'activity': 'Vestas Access Tracks Handover Check Sheet',
                'criteria': [
                    '"Annex 10_0054-6051_V10.16" — Vestas Internal Access Track Handover Check Sheet to be completed in full.',
                ],
                'rows': [
                    {'inspection': 'DR', 'frequency': 'Once'},
                    {'inspection': 'C',  'frequency': 'Once'},
                ],
                'lucas_codes': ['DR', 'C'],
                'client_codes': ['C'],
                'hold_witness': None,
            },
            {
                'no': '7',
                'activity': 'Rehabilitation',
                'criteria': [
                    'Topsoil respreads as detailed in the Vestas CEMP.',
                ],
                'rows': [
                    {'inspection': 'M', 'frequency': 'As required'},
                ],
                'lucas_codes': ['M'],
                'client_codes': [],
                'hold_witness': None,
            },
            {
                'no': '8',
                'activity': 'Guide Posts (Delineators)',
                'criteria': [
                    'As per Table 4 Earthworks Specification Section B 14.',
                ],
                'rows': [
                    {'inspection': 'M', 'frequency': 'As required'},
                ],
                'lucas_codes': ['M'],
                'client_codes': [],
                'hold_witness': None,
            },
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    'ITP03': {
        'itp_number': '03',
        'name':       'ITP#3 – Hardstand, Crane Pads & Blade Laydowns',
        'revision':   'E',
        'date':       '17/10/2025',
        'works':      'Hardstand / Crane Pads / Blade Laydown',
        'spec':       'KRWF-SPE-CIV-3000',
        'scope':      ('Hardstand/Crane pads/blade laydowns beside the WTG foundation including '
                       'survey set out, earthworks, subgrade preparation, granular pavement '
                       'construction and finishing.'),
        'prepared_by':  'Ehab Yousef',
        'approved_by':  'Wayne Hoey',
        'items': [
            {
                'no': '1',
                'activity': 'Survey',
                'criteria': [
                    'Ensure latest versions of plans/drawings are used for set out/modelling.',
                    'Extent of area to be marked with Pegs or incorporated into machine guidance prior to works commencing.',
                ],
                'rows': [
                    {'inspection': 'DR', 'frequency': 'As required'},
                    {'inspection': 'C',  'frequency': 'Once'},
                ],
                'lucas_codes': ['DR', 'C'],
                'client_codes': [],
                'hold_witness': None,
            },
            {
                'no': '2',
                'activity': 'Earthworks',
                'criteria': [
                    'Barricade the excavation zone as required.',
                    'Site won material to be used as cut to fill.',
                    'All existing fill within the construction area shall be completely sorted of all deleterious material prior to replacement.',
                    'Any loose, dry surface soil remaining after compaction shall be watered to achieve the specified moisture content immediately before placing the subsequent layer.',
                    'Excavate as specified in the drawings, aiming to reach the design subgrade.',
                    'Earthworks foundation: DCP tests — 6 per Hardstand, 1 per ancillary pad.',
                    'General fill placed in layers ≤300 mm (loose) compacted to: 92% DDR within 300 mm of finished pavement; 90% DDR more than 300 mm below.',
                    'Test frequency: every second layer within 800 mm of finished subgrade; every third layer greater than 800 mm below.',
                    'Proof roll every 3–4 layers where fill embankment >800 mm below finished subgrade; every second layer within 800 mm.',
                ],
                'rows': [
                    {'inspection': 'M', 'frequency': 'Once'},
                    {'inspection': 'M', 'frequency': 'Progressively'},
                    {'inspection': 'M', 'frequency': 'As required'},
                    {'inspection': 'M', 'frequency': 'Progressively'},
                    {'inspection': 'M', 'frequency': 'Progressively'},
                    {'inspection': 'T', 'frequency': '6/Hardstand, 1/ancillary pad'},
                    {'inspection': 'T', 'frequency': 'As specified'},
                ],
                'lucas_codes': ['M', 'T'],
                'client_codes': [],
                'hold_witness': None,
            },
            {
                'no': '3',
                'activity': 'Subgrade Preparation',
                'criteria': [
                    'Fill placed in continuous, approximately horizontal layers ≤200 mm thickness after compaction.',
                    'Excavated areas finished to even surface, trimmed to lines, levels, grades and batters per drawings.',
                    'Subgrade layer compacted to not less than 92% DDR.',
                    'Test frequency: 1 test/hardstand, 1 test/pair of crane pads, 1 test/blade laydown.',
                    'Survey pick up for the subgrade layer.',
                    'Proof roll of the subgrade, prior to commencing pavement construction.',
                ],
                'rows': [
                    {'inspection': 'M',   'frequency': 'Progressively'},
                    {'inspection': 'M',   'frequency': 'Progressively'},
                    {'inspection': 'T',   'frequency': 'As required'},
                    {'inspection': 'T',   'frequency': 'As required'},
                    {'inspection': 'M',   'frequency': 'As required'},
                    {'inspection': 'H&W', 'frequency': 'As required'},
                ],
                'lucas_codes': ['M', 'T'],
                'client_codes': ['H', 'W'],
                'hold_witness': 'H&W',
            },
            {
                'no': '4',
                'activity': 'Granular Pavement (Hardstand / Crane Pads / Blade Laydown)',
                'criteria': [
                    'Subbase material (minus 50 mm) — Class 4 (Austroads Part 4A): CBR ≥ 30%, PI = 9% ± 4%, Wet strength ≥ 40 kN. 1 test/10000 tonnes.',
                    'Basecourse material (minus 30 mm) — Class 3 (Austroads Part 4A): CBR ≥ 45%, PI = 9% ± 2%, Wet strength ≥ 50 kN. 1 test/5000 tonnes.',
                    'Spread as a uniform homogeneous layer to compacted layer thickness as per design.',
                    'Compact to minimum 96% DDR Subbase, 99% DDR Base.',
                    'Test frequency: Hardstand Subbase 2 tests, Hardstand Base 3 tests; Crane pad Subbase 1, Base 1; Blade laydown 1+1.',
                    'Plate load test conducted by qualified geotech contractor per KRWF-SPE-CIV-3000 Appendix C.',
                    'Proof rolling of granular pavement layer at the time of Client inspection.',
                ],
                'rows': [
                    {'inspection': 'T',   'frequency': '1/10000 t (subbase)'},
                    {'inspection': 'T',   'frequency': '1/5000 t (base)'},
                    {'inspection': 'M',   'frequency': 'Progressively'},
                    {'inspection': 'T',   'frequency': 'As required'},
                    {'inspection': 'T&W', 'frequency': '3 tests/hardstand'},
                    {'inspection': 'H&W', 'frequency': 'As required'},
                ],
                'lucas_codes': ['T', 'M'],
                'client_codes': ['H', 'W'],
                'hold_witness': 'H&W',
            },
            {
                'no': '5',
                'activity': 'Finishing',
                'criteria': [
                    'Survey picks up for the finish level and complete as-built conformance report.',
                ],
                'rows': [
                    {'inspection': 'C&W', 'frequency': 'As required'},
                ],
                'lucas_codes': ['C'],
                'client_codes': ['W'],
                'hold_witness': 'W',
            },
            {
                'no': '6',
                'activity': 'Vestas Hardstand Handover Check Sheet',
                'criteria': [
                    '"Annex 11_0050-8073_V12.12 – Crane Pads Requirements" — Vestas Hardstand Handover Check Sheet completed in full.',
                ],
                'rows': [
                    {'inspection': 'DR', 'frequency': 'Once per handover'},
                    {'inspection': 'C',  'frequency': 'Once per handover'},
                ],
                'lucas_codes': ['DR', 'C'],
                'client_codes': ['C'],
                'hold_witness': None,
            },
            {
                'no': '7',
                'activity': 'Road Drainage',
                'criteria': [
                    'Drainage installed as per design.',
                ],
                'rows': [
                    {'inspection': 'M', 'frequency': 'As required'},
                ],
                'lucas_codes': ['M'],
                'client_codes': [],
                'hold_witness': None,
            },
            {
                'no': '8',
                'activity': 'Rehabilitation',
                'criteria': [
                    'Topsoil respreads as detailed in the Vestas CEMP.',
                ],
                'rows': [
                    {'inspection': 'M', 'frequency': 'As required'},
                ],
                'lucas_codes': ['M'],
                'client_codes': [],
                'hold_witness': None,
            },
        ],
    },
}
