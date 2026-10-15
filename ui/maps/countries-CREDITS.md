# 离线国家与地区目录

`countries.json` 包含 238 个具有有效 ISO 3166-1 alpha-2 代码的国家与地区，字段为 `code`、简体中文 `name`、`lat`、`lon`。香港、澳门、台湾分别以 `HK`、`MO`、`TW` 保留在同一目录中；本目录不改写照片或城市原有的国家代码。

## 地理坐标来源

- Natural Earth 1:10m Admin 0 Countries，公共领域数据。
- [原始 GeoJSON](https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_0_countries.geojson)
- [维护仓库](https://github.com/nvkelso/natural-earth-vector/blob/master/geojson/ne_10m_admin_0_countries.geojson)
- [Natural Earth 使用条款](https://www.naturalearthdata.com/about/terms-of-use/)
- 获取日期：2026-10-15。
- 原始文件 SHA-256：`239eec57ac17f100a11e2536cffc56752c318b50ae765b0918ff7aab4ce8f255`。

## 名称与转换

1. 国家和地区代码优先取 Natural Earth 的有效 `ISO_A2`。法国、挪威、台湾的特殊记录，分别由原记录 `FRA`、`NOR`、`TWN` 对应的 `ISO_A2_EH` 恢复为 `FR`、`NO`、`TW`。不为其他没有标准代码的绘图单元自行编造代码。
2. 简体中文名称来自本机 Babel 2.17.0 的 `zh_Hans` 地区名称，即 Unicode CLDR 本地化资料；不使用来源 GeoJSON 中可能存在编码问题的名称。[Babel](https://babel.pocoo.org/)、[Unicode CLDR](https://cldr.unicode.org/)、[Unicode 数据使用许可](https://www.unicode.org/license.txt)。
3. 227 条坐标取 Natural Earth `LABEL_Y` / `LABEL_X`，并用原始多边形核验代表点在陆地范围内。
4. `GQ`、`NZ`、`TT`、`VC`、`AG`、`ST`、`GS` 的原标签点位于其多边形外，改为最大陆地多边形的内部代表点。多边形计算使用 Shapely；这里的主多边形按经纬度平面面积选择，未计算测地面积。
5. 四个常用国家采用明确的展示中心：`CN` 为纬度 35、经度 105；`FR` 为 46、2；`GB` 为 54、−2；`US` 为 39、−98。这些是国家聚合展示点，不是城市坐标。
6. 经纬度保留六位小数，记录按 alpha-2 代码排序。未将完整 12.7 MB 源 GeoJSON 放入产品；产品仅使用精简目录。

原始资料与可重现构建脚本保存在开发目录 `.qa-journey`；产品运行时不需要联网，也不需要 Babel 或 Shapely。
