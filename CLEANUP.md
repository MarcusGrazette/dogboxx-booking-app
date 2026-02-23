# Cleanup Plan — Dog Walking Booking App

## Phase 1: Remove Legacy Routes (biggest impact)
- [ ] Delete `app/routes.py`
- [ ] Delete `register_routes()` call in `app/__init__.py`
- [ ] Update `login_manager.login_view` to `"auth.login"` in `app/__init__.py`
- [ ] Update all `url_for()` calls in templates to use blueprint endpoints:
  - [ ] `layout.html`: index → client.index, login → auth.login, logout → auth.logout, register → auth.register, profile → client.profile
  - [ ] `index.html`: form action and cancel_booking → client endpoints
  - [ ] `login.html`: register link → auth.register
  - [ ] `admin.html`: any legacy route references
  - [ ] Any other templates referencing legacy endpoints
- [ ] Update `before_request` hook if it references legacy endpoints
- [ ] Verify the client blueprint serves `/` (not just `/client/`) or add a root redirect

## Phase 2: Delete Dead Code
- [ ] Delete `app/blueprints/admin/routes_helper.py` (unused HTML generator)
- [ ] Delete dead code in `app/blueprints/admin/routes.py` (unreachable code after return statements in `bookings_by_date()` and `assign_walker()`)
- [ ] Delete or rewrite `app/blueprints/api/routes.py` (every model reference is wrong)
- [ ] Delete unused templates:
  - [ ] `app/templates/logout.html`
  - [ ] `app/templates/_flashes.html`
  - [ ] `app/templates/client_dashboard.html`
  - [ ] `cover.html` (project root)

## Phase 3: Security Fixes
- [ ] Add `.env` to `.gitignore`
- [ ] Create `.env.example` with placeholder values
- [ ] Fix `'Cancelled'` → `'cancelled'` case bug in `walker/routes.py`
- [ ] Remove `logging.basicConfig(level=logging.DEBUG)` from any remaining files

## Phase 4: DRY Up Repeated Patterns
- [ ] Create `@admin_required` decorator (replace per-route role checks in admin routes)
- [ ] Create `@walker_required` decorator (same for walker routes)
- [ ] Extract image upload/PIL processing to shared `app/utils/upload.py`
- [ ] Consolidate `_redirect_by_role()` to one location (e.g. `app/utils/auth.py`)
- [ ] Consolidate `UPLOAD_FOLDER` config to one place

## Phase 5: Database
- [ ] Add indexes: `Booking.date`, `Booking.user_id`, `Booking.walker_id`, `Booking.status`
- [ ] Add indexes: `DogOwner.user_id`, `DogOwner.dog_id`, `WalkerSchedule.walker_id`
- [ ] Generate and apply migration for new indexes
- [ ] Fix `seeder.py`: remove `firstname`/`lastname` from Walker constructor
- [ ] Fix `seeder.py`: use DogOwner lookup instead of `dog.user_id`
- [ ] Fix `seeder.py`: look up service_type by slug instead of hardcoded ID 1
- [ ] Document seed scripts in README (seed.py = prod init, seeder.py = dev data)

## Phase 6: Frontend Cleanup
- [ ] Move FilePond CSS/JS includes from `layout.html` to onboarding template only
- [ ] Remove duplicate `reusable-calendar.css` include from `admin.html`
- [ ] Fix dog image paths in admin templates (`/static/images/` → `/static/uploads/dogs/`)
- [ ] Extract duplicate `toggleClientStatus`/`toggleWalkerStatus` JS to shared function

## Phase 7: Config Cleanup
- [ ] Remove unused `DROPZONE_*` config from `config.py`
- [ ] Remove or initialise `flask-cors` (in requirements.txt but unused)
- [ ] Add `psycopg2-binary` to `requirements.txt` if missing
- [ ] Remove duplicate `_redirect_by_role()` from `auth/routes.py` (after Phase 4)

## Phase 8: Verify
- [ ] Run the app and test: login, admin dashboard, calendar, booking, onboarding
- [ ] Check all navbar links work
- [ ] Check flash messages still display
- [ ] Check admin drag-drop allocation still works
- [ ] Check password change flow still works
