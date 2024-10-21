def udf(
    bbox: fused.types.TileGDF = None,
    year: int = 2023,
    variable: str = "evi2",
):
    import odc.stac
    import palettable
    import stacrs
    import utils
    from pystac import Item

    item_dicts = stacrs.search(
        "https://data.ldn.auspatious.com/geo_ls_lp/geo_ls_lp_0_1_0.parquet",
        bbox=bbox.total_bounds,
        datetime=f"{year}-01-01T00:00:00.000Z/{year}-12-31T23:59:59.999Z",
    )

    items = [Item.from_dict(d) for d in item_dicts]

    # Calculate the resolution based on zoom level.
    power = 13 - bbox.z[0]
    if power < 0:
        resolution = 30
    else:
        resolution = int(20 * 2**power)

    # Load the data into an XArray dataset
    data = odc.stac.load(
        items,
        crs="EPSG:3857",
        bands=[variable],
        resolution=resolution,
        bbox=bbox.total_bounds,
    ).squeeze()

    # Create a mask where data is nan
    mask = (~data.evi2.isnull()).squeeze().to_numpy()

    # Visualize that data as an RGB image.
    rgb_image = utils.visualize(
        data=data[variable],
        mask=mask,
        min=0,
        max=360,
        colormap=palettable.matplotlib.Viridis_20,
    )
    return rgb_image
