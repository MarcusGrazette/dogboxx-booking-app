"""Uploads-volume vs. R2-backup reconciliation.

Used by the `flask reconcile-uploads` CLI command (run from a dedicated
Railway cron service, since Railway volumes can only be mounted to one
service — this one has no volume of its own and reads the real uploads
volume via `web`'s /api/internal/uploads-manifest over the private network).
"""

import os

import requests


def fetch_volume_manifest(internal_url: str, secret: str) -> dict:
    """Fetch the {path: size} manifest of real files on the uploads volume."""
    resp = requests.get(
        f"{internal_url}/api/internal/uploads-manifest",
        headers={"X-Internal-Secret": secret},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_r2_manifest(bucket: str) -> dict:
    """Fetch the {path: size} manifest of every object in the R2 backup bucket."""
    import boto3

    client = boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT_URL"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )
    manifest = {}
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            manifest[obj["Key"]] = obj["Size"]
    return manifest


def diff_manifests(volume_files: dict, r2_files: dict) -> tuple[list, list, list]:
    """Compare a volume manifest against an R2 manifest.

    Returns (missing_from_r2, orphaned_in_r2, size_mismatches) — all sorted
    lists of keys. `missing_from_r2` is the actionable case: a real upload
    whose backup silently failed. `orphaned_in_r2` is expected background
    noise (R2 keeps backups for dogs/photos later deleted from the app) and
    not itself a problem.
    """
    volume_keys = set(volume_files)
    r2_keys = set(r2_files)

    missing_from_r2 = sorted(volume_keys - r2_keys)
    orphaned_in_r2 = sorted(r2_keys - volume_keys)
    size_mismatches = sorted(
        key for key in (volume_keys & r2_keys)
        if volume_files[key] != r2_files[key]
    )
    return missing_from_r2, orphaned_in_r2, size_mismatches
