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

To populate the catalogue/map presentation data without creating leads, visits, or subscriptions:

```bash
cd src/saashome
python manage.py seed_directory_coverage
```

The command adds 20 additional real-brand examples and gives every seeded profile 10 locations
labelled as demonstrative map areas. They are intentionally not presented as verified operating locations.

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

| Area | Guest | User | Vendor member | Organization admin | Organization owner | Staff |
| --- | --- | --- | --- | --- | --- | --- |
| Public franchise and content pages | View | View | View | View | View | View |
| Request franchise information | Create | Create | Create | Create | Create | Create |
| Profile, saved list and comparison | Login required | Own data | Own data | Own data | Own data | Own data |
| Vendor dashboard and own organization data | No | No | View | View | View | All organizations |
| Own organization's lead inbox and statuses | No | No | Manage | Manage | Manage | All organizations |
| Edit own franchise profile and media | No | No | Read only | Manage | Manage | Manage all |
| View subscriptions for own franchises | No | No | View | View | View | View all |
| Buy, change, extend or cancel a subscription | No | No | No | No | Manage | Manage all |
| Global `/leads/`, visits and management tools | No | No | No | No | No | Full access |
| Django `/admin/` | No | No | No | No | No | Staff permissions apply |

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

## Per-franchise vendor subscriptions

Paid access is assigned to a single franchise profile, not to the whole
organization. Vendor pages:

- `/subscriptions/` - plans, validity dates and pending changes;
- `/pricing/vendor/` - comparison of `Profil`, `Promocja` and `Pro`;
- `/vendor/franchises/<slug>/media/` - moderated gallery and documents;
- `/manage/subscription-requests/` - staff approval of vendor requests.

Only organization owners can start, extend, change or cancel a plan. Organization
admins and members have read-only billing access. Cancellation takes effect at
the end of the paid period. Plans with Stripe Price IDs use hosted Stripe
Checkout and Customer Portal. Plans without those IDs keep the manual
staff-approved fallback.

Organization roles decide *who may perform an action*. The franchise plan decides
*which product features are available*: Free masks lead contact details, Profil
unlocks them, Promocja adds promotion and analytics, and Pro adds the broadest
profile and reporting limits. A higher plan never grants organization-owner
permissions.

Plan capabilities are enforced server-side. They control description and file
limits, lead contact access, analytics, website/documents visibility, list
promotion and Pro profile highlights.

```bash
cd src/saashome
python manage.py migrate
python manage.py seed_plans  # optional; migrations already create default plans
```

## Stripe billing MVP

Stripe billing is scoped per franchise. A single organization has one Stripe
Customer and can have separate subscriptions for its franchise profiles. The
application never stores card details. A signed webhook is the source of truth;
returning from Checkout does not activate access by itself.

Required environment variables:

```bash
STRIPE_PUBLISHABLE_KEY=pk_test_...
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
BILLING_SUCCESS_URL=http://127.0.0.1:8000/billing/success/?session_id={CHECKOUT_SESSION_ID}
BILLING_CANCEL_URL=http://127.0.0.1:8000/pricing/vendor/
```

Create/reuse test-mode Stripe products and recurring prices, then save their IDs
on the plans automatically:

```bash
cd src/saashome
python manage.py seed_plans
python manage.py setup_stripe_test_catalog --confirm-test-mode
```

For local webhooks, install the Stripe CLI and run:

```bash
stripe listen --forward-to http://127.0.0.1:8000/billing/webhooks/stripe/
```

Put the displayed `whsec_...` value in `STRIPE_WEBHOOK_SECRET` and restart
Django. Configure Customer Portal in the Stripe test dashboard before testing
the "Zarządzaj w Stripe" button. Useful pages:

- `/pricing/vendor/` - start Checkout for a selected franchise;
- `/vendor/billing/` - current plans, status and period end;
- `/billing/webhooks/stripe/` - signed Stripe webhook endpoint.

In Stripe test mode use card `4242 4242 4242 4242`, any future expiry date and
any CVC. For Railway, create a public webhook pointing to
`https://YOUR-DOMAIN/billing/webhooks/stripe/`, subscribe to Checkout Session
Completed and Customer Subscription created/updated/deleted events, and set the
production-domain success/cancel URLs in Railway variables.

Create a repeatable local permission/billing dataset:

```bash
python manage.py seed_billing_demo
```

The command creates `demo.viewer@example.com`, `demo.owner@example.com`,
`demo.admin@example.com`, `demo.member@example.com`,
`demo.other.owner@example.com` and `demo.staff@example.com`. The development
password defaults to `DemoTest123!` and can be changed with
`DEMO_USER_PASSWORD`. These accounts are development-only.

## SEO foundation

Public pages now use a shared SEO layer with clean canonical URLs, Open Graph
metadata, JSON-LD, breadcrumbs and a controlled sitemap. Filtered catalog URLs
remain available to users but return `noindex,follow`, so arbitrary search and
filter combinations do not become duplicate search-engine pages. Private areas
such as vendor, saved, internal analytics, accounts and billing success routes
return `noindex,nofollow` through the shared template context.

Curated public SEO pages:

- `/franczyzy/k/<category-slug>/` - category landing pages;
- `/franczyzy/budzet/do-100000-zl/` - budget landing pages;
- `/franczyzy/model/online/` - business-model landing pages;
- `/metodologia/` and `/jak-to-dziala/` - trust pages;
- `/sitemap.xml` and `/robots.txt` - technical SEO endpoints.

The budget and business-model definitions live in
`franchises/seo_pages.py`. They are deliberately code-controlled, so the site
does not create thousands of thin pages from user filters. Article and landing
page SEO fields are grouped in Django Admin with recommended title and
description lengths.
