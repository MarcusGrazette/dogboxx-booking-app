# Cleanup Plan — Dog Walking Booking App

## Phase 1: Remove Legacy Routes ✅
- [x] Delete `app/routes.py`
- [x] Delete `register_routes()` call in `app/__init__.py`
- [x] Update `login_manager.login_view` to `"auth.login"` in `app/__init__.py`
- [x] Update all `url_for()` calls in templates to use blueprint endpoints
- [x] Move 429 error handler and csrf context_processor to `__init__.py`
- [x] Change client blueprint to serve from `/` (not `/client/`)

## Phase 2: Delete Dead Code ✅
- [x] Delete `app/blueprints/admin/routes_helper.py`
- [x] Delete dead code in `app/blueprints/admin/routes.py` (unreachable HTML builder)
- [x] Replace broken `app/blueprints/api/routes.py` with clean placeholder
- [x] Delete unused templates: logout.html, _flashes.html, client_dashboard.html, cover.html

## Phase 3: Security Fixes ✅
- [x] Create `.env.example` with placeholder values (.env already in .gitignore)
- [x] Fix `'Cancelled'` → `'cancelled'` case bug in `walker/routes.py`
- [x] Fix broken `joinedload(Booking.client)` in walker routes

## Phase 4: DRY Up Repeated Patterns ✅ (partial)
- [x] Create `@admin_required` decorator (replaces 12 inline role checks)
- [x] Create `@walker_required` decorator
- [ ] Extract image upload/PIL processing to shared `app/utils/upload.py`
- [ ] Consolidate `_redirect_by_role()` to one location

## Phase 5: Database
- [ ] Add indexes: `Booking.date`, `Booking.user_id`, `Booking.walker_id`, `Booking.status`
- [ ] Add indexes: `DogOwner.user_id`, `DogOwner.dog_id`, `WalkerSchedule.walker_id`
- [ ] Generate and apply migration for new indexes
- [ ] Fix `seeder.py`: remove `firstname`/`lastname` from Walker constructor
- [ ] Fix `seeder.py`: use DogOwner lookup instead of `dog.user_id`
- [ ] Fix `seeder.py`: look up service_type by slug instead of hardcoded ID 1
- [ ] Document seed scripts in README

## Phase 6: Frontend Cleanup ✅ (partial)
- [x] Move FilePond CSS/JS includes from `layout.html` to onboarding template only
- [x] Remove duplicate `reusable-calendar.css` include from `admin.html`
- [x] Fix dog image paths in admin JS (`/static/images/` → `/static/uploads/dogs/`)
- [ ] Extract duplicate `toggleClientStatus`/`toggleWalkerStatus` JS to shared function

## Phase 7: Config Cleanup ✅
- [x] Remove unused `DROPZONE_*` config, fix `UPLOAD_FOLDER` path
- [x] Remove unused `flask-cors` from requirements.txt
- [x] Add `psycopg2-binary` to requirements.txt

## Phase 8: Verify
- [ ] Run the app and test: login, admin dashboard, calendar, booking, onboarding
- [ ] Check all navbar links work
- [ ] Check flash messages still display
- [ ] Check admin drag-drop allocation still works
- [ ] Check password change flow still works
