"""Split download file path into base + relative path.

`download_files.file_path` previously stored the absolute container path
(e.g. ``/app/sync/202608/xxx.mp4``). This migration adds a ``file_base``
column so each record stores the container-absolute download directory
(derived from the config ``path`` template, e.g. ``/app/sync/202608``) plus
the relative path below that directory (``file_path``, e.g. ``xxx.mp4``).

Storing the base and relative part separately lets an operator later switch
``file_base`` when the docker volume mapping changes and keep records pointing
at the relocated files.

Legacy rows (if any) keep ``file_base = NULL`` and ``file_path`` as the full
path; consumers must treat ``file_path`` as relative whenever ``file_base`` is
set.
"""

VERSION = 4

STATEMENTS = ("ALTER TABLE download_files ADD COLUMN file_base TEXT",)
