"""
Regression test for add_security_headers (audit M19): the header logic
lives behind `if not app.debug and not app.testing`, so TESTING=True alone
already skips HSTS/X-Content-Type-Options/X-Frame-Options for the whole
suite. TestingConfig used to also set DEBUG=True, which was redundant (TESTING
already gates it) and actively harmful: it meant no test could ever assert
those headers exist, even by temporarily flipping app.testing off for one
request, since app.debug would still block it. With DEBUG=False, that
became possible — this test exercises it.
"""


def test_security_headers_present_when_not_testing(app, client):
    assert app.debug is False, (
        "TestingConfig.DEBUG must stay False — with it True this test "
        "cannot exercise the header-adding branch of add_security_headers "
        "at all (see the module docstring)."
    )

    app.testing = False
    try:
        resp = client.get('/auth/login')
    finally:
        app.testing = True  # restore immediately — other tests share this app instance

    assert resp.headers.get('Strict-Transport-Security') == 'max-age=31536000; includeSubDomains'
    assert resp.headers.get('X-Content-Type-Options') == 'nosniff'
    assert resp.headers.get('X-Frame-Options') == 'SAMEORIGIN'


def test_security_headers_absent_under_normal_test_config(client):
    """Sanity check for the gate itself: under the suite's actual TESTING=True
    config (DEBUG=False, untouched), these headers must NOT appear — proves
    the previous test's assertions are meaningful, not just always-true."""
    resp = client.get('/auth/login')
    assert 'Strict-Transport-Security' not in resp.headers
    assert 'X-Content-Type-Options' not in resp.headers
    assert 'X-Frame-Options' not in resp.headers
