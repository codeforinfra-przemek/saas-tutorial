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
`django-allauth` is installed and migrated as the foundation for email login and
future social login. Public auth screens now use `django-allauth-ui` with
`django-widget-tweaks`/`slippers` for styled forms. Allauth URLs are mounted
under `/auth/`, while the local `/accounts/` app keeps product pages such as the
dashboard and profile.

We are **not switching to a custom `AUTH_USER_MODEL` at this stage** because the
project already has applied `auth` migrations, a superuser, visit tracking tied
to `settings.AUTH_USER_MODEL`, and an existing accounts profile migration. In a
live database this is no longer a safe small change. A future custom user model
would require a planned migration/reset strategy.

Current minimal accounts design:

- registration with email and password via `/auth/signup/`
- new accounts start as inactive until the user clicks the email activation link
- login with email and password via `/auth/login/`
- logout via `/auth/logout/`
- password reset via `/auth/password/reset/`
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
- django-allauth setup:
  - regular account support
  - socialaccount support
  - styled allauth UI templates from `django-allauth-ui`
  - allauth URL namespace: `/auth/`
  - GitHub OAuth credentials are loaded from environment variables

### GitHub login

GitHub sign-in uses a GitHub **OAuth App**, not a GitHub App installation. Set
`GITHUB_OAUTH_CLIENT_ID` and `GITHUB_OAUTH_CLIENT_SECRET` in local `.env` and
Railway variables. The allauth callback URL is:

```text
http://127.0.0.1:8000/auth/github/login/callback/
```

For Railway, use the deployed domain with the same path, for example:

```text
https://saas-tutorial-production-e29b.up.railway.app/auth/github/login/callback/
```

GitHub login is treated as email-verified. A verified GitHub email matching an
existing account may sign in through GitHub or the existing password.

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

### Reference data and sources

Most seed records are intentionally marked as demo data. Do not treat their investment,
growth, or unit-economics values as an offer from a franchisor.

McDonald's is the first profile with selected public reference data. It records only values
that can be supported by current official McDonald's Poland pages, keeps unsupported fields
empty, and removes generated demo map points. It does **not** import an asserted complete list
of restaurants: the official restaurant locator is dynamic and does not publish a stable public
export that can be treated as a current source of truth.

Update the existing McDonald's record without resetting other demo data:

```bash
cd src/saashome
python manage.py enrich_mcdonalds_reference
```

### Franchise data and comparison

The public comparison table is available at `/franchises/directory/`. It has no
map and supports filters for investment, business type, network growth, mature
unit revenue, payback period, financing, and financial-data availability.

The extended profile fields are inspired by the US Franchise Disclosure Document
(FDD): investment and fees, support/territory, agreement terms, financial
performance, and outlet statistics. This project is not a substitute for a
legal FDD or investment advice. Seeded metrics use `data_status=demo` and must
never be presented as official brand disclosures.

To add the schema and populate the local development database:

```bash
cd src/saashome
python manage.py migrate
python manage.py seed_demo_data
```

## Leads MVP

The `leads` app stores one-franchise request-info submissions.

- form location: franchise detail page
- POST endpoint: `/franchises/<slug>/request-info/`
- admin model: `Lead`
- status flow:
  - `new`
  - `contacted`
  - `qualified`
  - `sent_to_vendor`
  - `rejected`
  - `closed`
- attribution captured:
  - source path
  - referrer
  - session key
  - UTM params
  - user agent
  - hashed IP

Optional notification email:

```bash
LEADS_NOTIFICATION_EMAIL=admin@example.com
```

## Saved Franchises / Comparison MVP

The `shortlists` app lets logged-in users save franchises and submit one request
to multiple saved franchises.

- saved list: `/saved/`
- comparison: `/saved/compare/`
- multi-request form: `/saved/request-info/`
- save/unsave actions use POST only:
  - `/franchises/<slug>/save/`
  - `/franchises/<slug>/unsave/`
- multi-request creates one normal `Lead` per selected franchise
- duplicate leads for the same email + franchise are skipped for 24 hours
- `Lead.multi_request_id` groups leads created from the same multi-request

After pulling shortlist changes:

```bash
cd src/saashome
python manage.py migrate
python manage.py runserver
```

## Access roles

Application access is based on server-side checks, not only on hidden menu links:

| Area | Guest | User | Vendor member | Staff/admin |
| --- | --- | --- | --- | --- |
| Public franchise and content pages | View | View | View | View |
| Request franchise information | Create | Create | Create | Create |
| Profile, saved list and comparison | Login required | Own data | Own data | Own data |
| Claim an unclaimed franchise | Login required | Create/view own claims | Create/view own claims | Create/view own claims |
| Vendor dashboard onboarding page | Login required | View empty onboarding state | View own organization data | View all active organizations |
| Vendor franchises, leads and analytics | No | No | Own active organizations only | All active organizations |
| Internal leads, visits and franchise management | No | No | No | Full access |
| Django `/admin/` | No | No | No | Staff permissions apply |

Vendor access comes from an active `OrganizationMembership` in an active
`Organization`. The editable profile `user_type` value is not an authorization
mechanism and users cannot grant themselves vendor access.

## Investor services

`/pricing/` now adapts to the visitor type:

- regular users and guests see investor services: a location report and a
  request form for expert matching;
- active vendor members and staff see franchise-provider packages;
- `/pricing/vendor/` is protected for active vendor members and staff;
- investor service requests are stored separately from franchise `Lead` records.

The location report currently uses a manual order confirmation. After a request,
the team confirms the scope and sends a payment link; no payment is processed by
the application yet.

After pulling these changes, run:

```bash
cd src/saashome
python manage.py migrate
```
