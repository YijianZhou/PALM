"""Shared helpers for PALM SageMaker PAL processing jobs."""

from pathlib import Path


def upload_tree(s3_client, local_root, bucket, key_prefix):
    local_root = Path(local_root)
    for path in sorted(local_root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            relative = path.relative_to(local_root).as_posix()
            key = key_prefix.rstrip("/") + "/" + relative
            s3_client.upload_file(str(path), bucket, key)


def prefix_has_objects(s3_client, bucket, prefix):
    response = s3_client.list_objects_v2(
        Bucket=bucket, Prefix=prefix.rstrip("/") + "/", MaxKeys=1
    )
    return bool(response.get("KeyCount", 0))
