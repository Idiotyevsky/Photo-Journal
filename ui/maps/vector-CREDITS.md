# Offline vector map sources

Both layers are Natural Earth public-domain data. They can be bundled and used offline; no online map service, account or key is required.

- [Natural Earth Terms of Use](https://www.naturalearthdata.com/about/terms-of-use/)
- [Natural Earth boundary policy](https://www.naturalearthdata.com/about/disputed-boundaries-policy/)

## World countries

- Layer: `vector-world.json`, Natural Earth 1:50m Admin 0 Countries.
- [Official layer page](https://www.naturalearthdata.com/downloads/50m-cultural-vectors/50m-admin-0-countries-2/)
- [GeoJSON in the Natural Earth maintainer repository](https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_admin_0_countries.geojson)
- Source SHA-256: `3e458fc036ad0a66411f2c1e6cac49c5d7bfb81cb1123bc513b22511a2b7fdeb`.
- Features: 242; vertices: 99613. IDs use source ADM0_A3; names use source NAME_ZH where available.

## China region administrative units

- Layer: `vector-china.json`, Natural Earth 1:10m Admin 1 States and Provinces.
- [Official layer page](https://www.naturalearthdata.com/downloads/10m-cultural-vectors/10m-admin-1-states-provinces/)
- [GeoJSON in the Natural Earth maintainer repository](https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_1_states_provinces.geojson)
- Source SHA-256: `22d0e3ad85eb3e27f17cabf8ba2d50e554fbc27a87796ff891d958185da62fb5`.
- Selection: source adm0_a3 is CHN, TWN, HKG or MAC; original admin-1 units are retained without merging. Counts: {"CHN": 32, "HKG": 18, "TWN": 21, "MAC": 1}. IDs use source adm1_code, with source name_zh/name labels.

## Coordinate handling and limitations

Downloaded on 2026-10-16. All geometries retain source longitude/latitude coordinates, polygon rings and detail. Only numeric rounding to four decimal places and property reduction were applied; there is no projection, smoothing, resampling or further geometry simplification. The layers are plain JSON objects with features[{id,name,geometry}] and attribution. They can be rendered directly by the app geographic SVG projection.

The admin-1 source includes mainland province-level units, and the source administrative units for Taiwan, Hong Kong and Macao. These do not form a uniform list of 34 province-level features. Natural Earth defaults to de facto boundaries and does not constitute an official Chinese boundary or maritime-claims map; disputed borders, small islands and maritime features require separate verification for any official-map use. The existing city gazetteer and saved footprints are unchanged.
