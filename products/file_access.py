def get_asset_file_name(asset):
    if hasattr(asset, "high_res_file") and asset.high_res_file:
        return asset.high_res_file.name
    if hasattr(asset, "video_asset_name") and asset.video_asset_name:
        return asset.video_asset_name
    if hasattr(asset, "video_file") and asset.video_file:
        return asset.video_file.name
    return None


def open_asset_file(asset, mode="rb"):
    if hasattr(asset, "high_res_file") and asset.high_res_file:
        try:
            asset.high_res_file.open(mode)
            return asset.high_res_file
        except Exception:
            return None
    if hasattr(asset, "open_video_asset"):
        try:
            file_handle = asset.open_video_asset(mode)
            if file_handle:
                return file_handle
        except Exception:
            return None
    if hasattr(asset, "video_file") and asset.video_file:
        try:
            asset.video_file.open(mode)
            return asset.video_file
        except Exception:
            return None
    return None
