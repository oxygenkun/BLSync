"""
DEPRECATED: This module is deprecated.

The functions already_download_bvids and already_download_bvids_add have been replaced
by using TaskDAL to query completed tasks in the task table.

For checking if a video is already downloaded, use:
    await task_dal.get_completed_bvids(favid)

This file is kept for reference only and will be removed in a future version.
"""
