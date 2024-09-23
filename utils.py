from odc.geo.gridspec import GridSpec

WGS84GRID10 = GridSpec("EPSG:4326", tile_shape=(15000, 15000), resolution=0.0001)
WGS84GRID30 = GridSpec("EPSG:4326", tile_shape=(5000, 5000), resolution=0.0003)

USGSCATALOG = "https://landsatlook.usgs.gov/stac-server/"
USGSLANDSAT = "landsat-c2l2-sr"

def http_to_s3_url(http_url):
    """Convert a USGS HTTP URL to an S3 URL"""
    s3_url = http_url.replace(
        "https://landsatlook.usgs.gov/data", "s3://usgs-landsat"
    ).rstrip(":1")
    return s3_url

