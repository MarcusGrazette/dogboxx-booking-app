"""Uploads-manifest route auth, and the reconciliation diff logic.

The route is exercised against a temporary directory (monkeypatched onto
app.static_folder) rather than the repo's real app/static/uploads, so tests
never touch real uploaded files. Network calls (the R2 listing, the HTTP
fetch of the manifest) are not exercised here — diff_manifests is a pure
function, tested directly.
"""
from app.utils.uploads_reconcile import diff_manifests


class TestUploadsManifestRoute:
    def test_requires_secret(self, client):
        resp = client.get('/api/internal/uploads-manifest')
        assert resp.status_code == 403

    def test_rejects_wrong_secret(self, client, app, monkeypatch):
        monkeypatch.setitem(app.config, 'INTERNAL_API_SECRET', 'correct-secret')
        resp = client.get(
            '/api/internal/uploads-manifest',
            headers={'X-Internal-Secret': 'wrong-secret'},
        )
        assert resp.status_code == 403

    def test_returns_manifest_excluding_defaults(self, client, app, monkeypatch, tmp_path):
        monkeypatch.setitem(app.config, 'INTERNAL_API_SECRET', 'correct-secret')

        (tmp_path / 'uploads' / 'dogs').mkdir(parents=True)
        (tmp_path / 'uploads' / 'profiles').mkdir(parents=True)
        (tmp_path / 'uploads' / 'dogs' / 'abc123.jpg').write_bytes(b'x' * 100)
        (tmp_path / 'uploads' / 'dogs' / 'default-dog.jpg').write_bytes(b'y' * 50)
        (tmp_path / 'uploads' / 'dogs' / 'default-dog.png').write_bytes(b'y' * 50)
        (tmp_path / 'uploads' / 'profiles' / 'def456.jpg').write_bytes(b'z' * 200)
        monkeypatch.setattr(app, 'static_folder', str(tmp_path))

        resp = client.get(
            '/api/internal/uploads-manifest',
            headers={'X-Internal-Secret': 'correct-secret'},
        )
        assert resp.status_code == 200
        assert resp.get_json() == {
            'dogs/abc123.jpg': 100,
            'profiles/def456.jpg': 200,
        }


class TestDiffManifests:
    def test_all_matching(self):
        volume = {'dogs/a.jpg': 100, 'profiles/b.jpg': 200}
        r2 = {'dogs/a.jpg': 100, 'profiles/b.jpg': 200}
        missing, orphaned, mismatches = diff_manifests(volume, r2)
        assert missing == []
        assert orphaned == []
        assert mismatches == []

    def test_missing_from_r2(self):
        volume = {'dogs/a.jpg': 100, 'dogs/b.jpg': 200}
        r2 = {'dogs/a.jpg': 100}
        missing, orphaned, mismatches = diff_manifests(volume, r2)
        assert missing == ['dogs/b.jpg']
        assert orphaned == []
        assert mismatches == []

    def test_orphaned_in_r2_is_not_a_problem_but_is_reported(self):
        volume = {'dogs/a.jpg': 100}
        r2 = {'dogs/a.jpg': 100, 'dogs/deleted-dog.jpg': 999}
        missing, orphaned, mismatches = diff_manifests(volume, r2)
        assert missing == []
        assert orphaned == ['dogs/deleted-dog.jpg']
        assert mismatches == []

    def test_size_mismatch(self):
        volume = {'dogs/a.jpg': 100}
        r2 = {'dogs/a.jpg': 20}
        missing, orphaned, mismatches = diff_manifests(volume, r2)
        assert missing == []
        assert orphaned == []
        assert mismatches == ['dogs/a.jpg']
