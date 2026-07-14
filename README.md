# Initial Setup:
```
python3 -m venv venv
source venv/bin/activate
cd src/
pwd
/home/prz/code/saas-tutorial/src
pip install -r ../requirements.txt 
django-admin startproject saashome
python manage.py runserver
python manage.py startapp visits
python manage.py makemigrations visits
python manage.py migrate
python manage.py collectstatic
```

## Accounts / Auth setup

The project uses Django's built-in `User` model with an email-first login flow.
`django-allauth` is installed and migrated as the foundation for future social
login. The current custom `/accounts/` views remain the primary login/signup UI
for now. Allauth URLs are mounted under `/allauth/` so we can add social
providers later without breaking the existing flow.

We are **not switching to a custom `AUTH_USER_MODEL` at this stage** because the
project already has applied `auth` migrations, a superuser, visit tracking tied
to `settings.AUTH_USER_MODEL`, and an existing accounts profile migration. In a
live database this is no longer a safe small change. A future custom user model
would require a planned migration/reset strategy.

Current minimal accounts design:

- registration with email and password
- new accounts start as inactive until the user clicks the email activation link
- login with email and password
- logout
- password reset using Django auth views
- dashboard after login: `/accounts/dashboard/`
- user profile: `/accounts/profile/`
- profile fields:
  - avatar/icon
  - user type: `user` or `vendor`
  - `email_verified` is set after successful email activation
  - headline, bio, location, website
- password change from the profile page
- organization/vendor account models:
  - `Organization`
  - `OrganizationMembership`
  - roles: `owner`, `admin`, `member`
- Django admin registration for user profiles and organizations
- django-allauth foundation:
  - regular account support
  - socialaccount support
  - allauth URL namespace: `/allauth/`
  - provider credentials can later be added via Django Admin `Social applications`

Important paths:

```txt
src/saashome/accounts/
src/saashome/templates/accounts/
src/saashome/templates/registration/
```

Run migrations after pulling account changes:

```bash
cd src/saashome
python manage.py migrate
```

## Franchises MVP

The `franchises` app powers the first directory/ranking view:

- list page: `/franchises/`
- detail page: `/franchises/<slug>/`
- models:
  - `FranchiseCategory`
  - `Franchise`
  - `FranchiseLocation`
- filters:
  - search query: `q`
  - category slug: `category`
  - max investment: `investment_max`
  - business type: `business_type`
- map:
  - Leaflet.js loaded from CDN
  - OpenStreetMap tiles
  - markers generated from active franchise locations

After pulling franchise changes:

```bash
cd src/saashome
python manage.py makemigrations franchises
python manage.py migrate
python manage.py runserver
```
