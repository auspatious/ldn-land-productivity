from typing import Tuple

import geopandas as gpd
import numpy as np
from odc.geo import Geometry
from odc.geo.geobox import GeoBox, GeoboxTiles
from xarray import Dataset

from affine import Affine

from pathlib import Path

WGS84GRID10 = GeoboxTiles(
    GeoBox(
        (1800000, 3600000), Affine(0.0001, 0.0, -180.0, 0.0, 0.0001, -90.0), "epsg:4326"
    ),
    (5000, 5000),
)
WGS84GRID30 = GeoboxTiles(
    GeoBox(
        (600000, 1200000), Affine(0.0003, 0.0, -180.0, 0.0, 0.0003, -90.0), "epsg:4326"
    ),
    (5000, 5000),
)

USGSCATALOG = "https://landsatlook.usgs.gov/stac-server/"
USGSLANDSAT = "landsat-c2l2-sr"


def get_tiles(grid=WGS84GRID30) -> list[Tuple[Tuple[int, int], GeoBox]]:
    """Get all the tiles for all AOIs, returning a list of (tile_index, geobox)"""
    # Load our extents
    this_folder = Path(__file__).parent
    gdf = gpd.read_file(this_folder / "aois.geojson")

    # 0 is Fiji, 1 is Caribbean and 2 is Belize
    fiji = list(
        grid.tiles(Geometry(gdf.geometry[0], crs="epsg:4326"))
    )
    carb = list(
        grid.tiles(Geometry(gdf.geometry[1], crs="epsg:4326"))
    )
    belz = list(
        grid.tiles(Geometry(gdf.geometry[2], crs="epsg:4326"))
    )

    # This is all the tiles
    tiles = fiji + carb + belz

    return [(tile, grid[tile]) for tile in tiles]


def get_tile_index(tile_index: int) -> Tuple[int, int]:
    return get_tiles()[tile_index][0]


def http_to_s3_url(http_url):
    """Convert a USGS HTTP URL to an S3 URL"""
    s3_url = http_url.replace(
        "https://landsatlook.usgs.gov/data", "s3://usgs-landsat"
    ).rstrip(":1")
    return s3_url


def mask_usgs_landsat(data: Dataset) -> Dataset:
    """Create cloud mask, scale values to 0-1 and set nodata to NaN"""
    # Bits 3 and 4 are cloud shadow and cloud, respectively.
    bitflags = 0b00011000

    # Bitwise AND to select any pixel that is cloud shadow or cloud or nodata
    cloud_mask = (data.qa_pixel & bitflags) != 0
    # Note that it might be a good idea to dilate the mask here to catch
    # any pixels that are adjacent to clouds

    # Pick out nodata too
    nodata_mask = data.qa_pixel == 0

    # Combined the masks
    mask = cloud_mask | nodata_mask

    # Mask the original data
    masked = data.where(~mask, other=np.nan).drop_vars("qa_pixel")

    # Scale the data to 0-1
    scaled = (masked.where(masked != 0) * 0.0000275 + -0.2).clip(0, 1)

    return scaled


def create_land_productivity_indices(data: Dataset, drop: bool = True) -> Dataset:
    """Create NDVI, MSAVI and EVI2 indices"""

    # NDVI
    data["ndvi"] = ((data["nir"] - data["red"]) / (data["nir"] + data["red"])).clip(
        -1, 1
    )

    # MSAVI
    data["msavi"] = 0.5 * (
        (2 * data["nir"] + 1)
        - np.sqrt((2 * data["nir"] + 1) ** 2 - 8 * (data["nir"] - data["red"]))
    ).clip(0, 1)

    # EVI2
    data["evi2"] = (
        2.5 * (data["nir"] - data["red"]) / (data["nir"] + 2.4 * data["red"] + 1)
    ).clip(0, 1)

    if drop:
        data = data.drop_vars(["red", "nir"])

    return data
