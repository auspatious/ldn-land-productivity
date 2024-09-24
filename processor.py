# Description: This file contains the processor class that is used to process the data

import json
from typing import Iterable
import numpy as np
from pystac_client import Client
from pystac import ItemCollection, Asset, Item
from odc.stac import load, configure_s3_access
from dask.distributed import Client as DaskClient
from xarray import Dataset

import boto3

from rio_stac import create_stac_item


from utils import (
    WGS84GRID30,
    USGSLANDSAT,
    USGSCATALOG,
    http_to_s3_url,
    mask_usgs_landsat,
    create_land_productivity_indices,
)

from odc.geo.geobox import GeoBox

from logging import getLogger


class LDNPRocessor:
    log = getLogger("LDN")
    log.setLevel("INFO")

    tile: Iterable = None
    geobox: GeoBox = None
    year: int = None
    bucket: str = None
    path: str = None

    items: ItemCollection = None
    data: Dataset = None
    results: Dataset = None

    collection: str = "geo-ls-lp"
    item: Item = None

    dask_config = {
        "n_workers": 4,
        "threads_per_worker": 1,
        "memory_limit": "16GB",
    }

    def __init__(
        self, tile: Iterable, year: int, bucket: str, path: str, dask_config: dict = None, *args, **kwargs
    ):
        self.tile = tile
        self.geobox = WGS84GRID30.tile_geobox(tile)
        self.tile = tile
        self.year = year
        self.bucket = bucket
        self.path = path

        if dask_config is not None:
            self.dask_config = dask_config

        configure_s3_access(cloud_defaults=True, requester_pays=True)

    @property
    def tile_id(self):
        return f"{self.collection}_{self.year}_{self.tile[0]}_{self.tile[1]}"

    def s3_path(self, var: str | None, ext: str = "tif"):
        name = self.tile_id if var is None else f"{self.tile_id}_{var}"
        file_path = (
            f"{self.path}/{self.year}/{self.tile[0]}/{self.tile[1]}/" f"{name}.{ext}"
        )

        return file_path

    def get_stac_item(self):
        assets = {
            var: Asset(
                media_type="image/tiff; application=geotiff; profile=cloud-optimized",
                href=self.s3_path(var),
                roles=["data"],
            )
            for var in self.results.datavars
        }

        return create_stac_item(
            assets.values()[0].href,
            id=self.tile_id,
            collection=self.collection,
            input_datetime=f"{self.year}-01-01T00:00:00Z",
            assets=assets,
            with_proj=True,
            with_raster=True,
        )

    def find(self):
        client = Client.open(USGSCATALOG)
        self.items = client.search(
            collections=[USGSLANDSAT],
            intersects=self.geobox.geographic_extent,
            datetime=f"{self.year - 1}-11/{self.year + 1}-01",
            query={"landsat:collection_category": {"in": ["T1"]}},
        ).item_collection()

        self.log.info(f"Found {len(self.items)} items")

    def load(self):
        if self.items is None:
            self.log.error("No items to load")

        self.log.info(f"Loading {len(self.items)} items")

        data = load(
            self.items,
            geobox=self.geobox,
            measurements=["red", "nir08", "qa_pixel"],
            chunks={"x": 2501, "y": 2501},
            groupby="solar_day",
            dtype="uint16",
            nodata=0,
            resampling={"qa_pixel": "nearest"},
            patch_url=http_to_s3_url,
        )

        data = data.rename_vars({"nir08": "nir"})

        self.log.info(
            f"Loaded data with shape lon: {data.sizes['longitude']} lat: {data.sizes['latitude']} time: {data.sizes['time']}"
        )

        self.data = data

    def transform(self):
        if self.data is None:
            self.log.error("No data to transform")

        masked = mask_usgs_landsat(self.data)
        indices = create_land_productivity_indices(masked, drop=True)
        monthly = indices.resample(time="1M").median()

        # Todo: see if this can be done with more fancy interpolation
        filled = monthly.bfill("time").ffill("time")

        # Interpolate daily data, so we can compute the integral
        daily = filled.resample(time="1D")
        interpolated = daily.interpolate("linear").sel(
            time=slice(f"{self.year}-01-01", f"{self.year}-12-31")
        )
        integral = interpolated.integrate("time", datetime_unit="D")

        # Todo: consider adding other statistics, like median or max

        self.log.info(
            f"Results processed with shape lon: {integral.sizes['longitude']} lat: {integral.sizes['latitude']}"
        )

        self.results = integral

    def write(self):
        if self.results is None:
            self.log.error("No results to save")

        written = []

        # Write files to S3 as cloud optimised GeoTIFFs
        with DaskClient(**self.dask_config):
            for var in self.results.data_vars:
                out_path = self.s3_path(var)
                self.log.info(f"Writing {var} to {out_path}")
                self.results[var].odc.write_cog(out_path, nodata=np.nan)
                written.append(var, out_path)

        # Create a STAC document
        stac_path = self.s3_path(None, "stac-item.json")
        item = self.get_stac_item()
        item.set_self_href(stac_path)

        self.item = item

        self.log.info(f"Writing STAC item to {stac_path}")
        stac_item_json = json.dumps(item.to_dict(), indent=4)
        s3 = boto3.client("s3")
        s3.put_object(
            Bucket=self.bucket,
            Key=stac_path,
            Body=stac_item_json,
            ContentType="application/json",
        )

        self.log.info(f"Saved {len(written)} files and STAC item")
