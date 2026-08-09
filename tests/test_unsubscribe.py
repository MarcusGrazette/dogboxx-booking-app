"""
Regression tests for /auth/unsubscribe/<token> (audit M17): a bare GET must
not mutate state — link scanners (Outlook SafeLinks, Proofpoint) and browser
prefetch fetch every URL in an email, which would silently unsubscribe
clients who never clicked. The write must only happen on POST.
"""
from werkzeug.security import generate_password_hash

from app import db
from app.models import User


def _make_subscribed_user(email='unsub_test@test.com'):
    u = User(
        firstname='Test', lastname='User', email=email, role='client',
        is_admin=False, hashed_password=generate_password_hash('Testpass1!'),
        active=True, email_marketing=True,
    )
    db.session.add(u)
    db.session.commit()
    return u


def test_get_does_not_unsubscribe(app, client):
    with app.app_context():
        user = _make_subscribed_user()
        token = user.make_unsubscribe_token()
        user_id = user.id

    resp = client.get(f'/auth/unsubscribe/{token}')
    assert resp.status_code == 200

    with app.app_context():
        assert db.session.get(User, user_id).email_marketing is True


def test_get_renders_confirmation_form(app, client):
    with app.app_context():
        user = _make_subscribed_user('unsub_form@test.com')
        token = user.make_unsubscribe_token()

    resp = client.get(f'/auth/unsubscribe/{token}')
    assert resp.status_code == 200
    assert b'Confirm unsubscribe' in resp.data


def test_post_unsubscribes(app, client):
    with app.app_context():
        user = _make_subscribed_user('unsub_post@test.com')
        token = user.make_unsubscribe_token()
        user_id = user.id

    resp = client.post(f'/auth/unsubscribe/{token}', follow_redirects=True)
    assert resp.status_code == 200

    with app.app_context():
        assert db.session.get(User, user_id).email_marketing is False


def test_get_on_already_unsubscribed_shows_already_done(app, client):
    with app.app_context():
        user = _make_subscribed_user('unsub_already@test.com')
        user.email_marketing = False
        db.session.commit()
        token = user.make_unsubscribe_token()

    resp = client.get(f'/auth/unsubscribe/{token}')
    assert resp.status_code == 200
    assert b'already unsubscribed' in resp.data.lower()


def test_invalid_token_redirects_to_login(app, client):
    resp = client.get('/auth/unsubscribe/not-a-real-token', follow_redirects=False)
    assert resp.status_code in (301, 302)
    assert '/auth/login' in resp.headers['Location']

    resp = client.post('/auth/unsubscribe/not-a-real-token', follow_redirects=False)
    assert resp.status_code in (301, 302)
    assert '/auth/login' in resp.headers['Location']
