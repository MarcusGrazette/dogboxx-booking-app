#!/bin/bash
set -e

# Persistent uploads via Railway volume
#
# When a volume is mounted at /data/uploads, replace the ephemeral
# app/static/uploads directory with a symlink so that:
#   - uploaded files survive deploys
#   - all existing /static/uploads/... URLs work unchanged
#   - local dev and CI (no volume) are completely unaffected
#
# Mount point configured in railway.toml: /data/uploads

echo "=== Volume diagnostics ==="
echo "/data exists:         $([ -e /data ] && echo YES || echo NO)"
echo "/data/uploads exists: $([ -e /data/uploads ] && echo YES || echo NO)"
echo "/data type:           $([ -d /data ] && echo dir || ([ -e /data ] && echo exists-not-dir || echo missing))"
ls /data 2>/dev/null && echo "Contents of /data shown above" || echo "/data not listable"
mount | grep -E '/data|upload' || echo "No /data or upload mounts found"
echo "==========================="

if [ -d /data/uploads ]; then
  mkdir -p /data/uploads/dogs /data/uploads/profiles

  # Seed bundled defaults to the volume on first deploy (cp -n = no-clobber)
  cp -n app/static/uploads/dogs/default-dog.png /data/uploads/dogs/ 2>/dev/null || true
  cp -n app/static/uploads/dogs/default-dog.jpg /data/uploads/dogs/ 2>/dev/null || true

  # Disaster recovery: if the volume is empty (e.g. after a volume loss and
  # recreation), restore all photos from the R2 backup before serving traffic.
  DOGS_COUNT=$(find /data/uploads/dogs -maxdepth 1 -name '*.jpg' -o -name '*.png' 2>/dev/null | grep -vc 'default-dog' || true)
  if [ "$DOGS_COUNT" -eq 0 ] && [ -n "$R2_ACCESS_KEY_ID" ]; then
    echo "Volume appears empty — restoring uploads from R2..."
    python3 - <<'PYEOF'
import boto3, os
from botocore.client import Config
client = boto3.client('s3',
    endpoint_url=os.environ['R2_ENDPOINT_URL'],
    aws_access_key_id=os.environ['R2_ACCESS_KEY_ID'],
    aws_secret_access_key=os.environ['R2_SECRET_ACCESS_KEY'],
    config=Config(signature_version='s3v4'),
    region_name='auto')
bucket = os.environ.get('R2_BUCKET_UPLOADS', 'dogboxx-uploads-backup')
paginator = client.get_paginator('list_objects_v2')
restored = 0
for page in paginator.paginate(Bucket=bucket):
    for obj in page.get('Contents', []):
        key = obj['Key']
        local_path = f'/data/uploads/{key}'
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        if not os.path.exists(local_path):
            client.download_file(bucket, key, local_path)
            restored += 1
print(f'Restored {restored} file(s) from R2.')
PYEOF
  fi

  # Swap the ephemeral directory for a symlink to the volume
  rm -rf app/static/uploads
  ln -s /data/uploads app/static/uploads
fi

flask db upgrade
flask seed-service-types

# exec replaces the shell so gunicorn receives Railway's SIGTERM directly
exec gunicorn run:app --workers 2 --worker-class gevent --worker-connections 100 --bind 0.0.0.0:$PORT --timeout 120 --log-level info --access-logfile -
