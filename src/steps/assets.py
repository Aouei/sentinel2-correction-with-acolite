import ee


def asset_exists(asset_id: str) -> bool:
    """True when the Earth Engine asset exists. Any other error is not swallowed.

    Earth Engine answers a missing asset with "Asset '...' does not exist or doesn't allow this
    operation", so that message is the only one treated as "not there".
    """
    try:
        ee.data.getAsset(asset_id)
    except ee.EEException as error:
        if "does not exist" in str(error):
            return False
        raise
    return True
