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

echo "Volume check: /data=$(ls -la /data 2>&1 | tr '\n' ' ')"

if [ -d /data/uploads ]; then
  mkdir -p /data/uploads/dogs /data/uploads/profiles

  # Seed bundled defaults to the volume on first deploy (cp -n = no-clobber)
  cp -n app/static/uploads/dogs/default-dog.png /data/uploads/dogs/ 2>/dev/null || true
  cp -n app/static/uploads/dogs/default-dog.jpg /data/uploads/dogs/ 2>/dev/null || true

  # Swap the ephemeral directory for a symlink to the volume
  rm -rf app/static/uploads
  ln -s /data/uploads app/static/uploads

  echo "Uploads directory linked to volume at /data/uploads"
fi

flask db upgrade

# exec replaces the shell so gunicorn receives Railway's SIGTERM directly
exec gunicorn run:app --workers 2 --bind 0.0.0.0:$PORT --timeout 120 --log-level info
