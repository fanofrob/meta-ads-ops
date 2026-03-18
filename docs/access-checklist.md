# API Access Checklist

Complete all items before running any scripts.

## Meta Developer Setup

- [ ] Created a Meta Developer account at developers.facebook.com
- [ ] Created a Meta App (type: Business)
- [ ] Added the **Marketing API** product to the app
- [ ] App is in **Live mode** (not Development mode) — required for production account access

## Permissions

- [ ] App has the `ads_read` permission approved
- [ ] App has the `ads_management` permission approved (required for Phase 2 automation)
- [ ] The user generating the access token has **Advertiser** or **Admin** role on the Ad Account in Business Manager

## Access Token

- [ ] Generated a **User Access Token** with the required permissions
- [ ] Token has been exchanged for a **Long-Lived Token** (valid ~60 days)
  - Short-lived tokens expire in 1 hour and will break scheduled jobs
  - See: [Meta docs — Long-Lived Tokens](https://developers.facebook.com/docs/facebook-login/guides/access-tokens/get-long-lived)
- [ ] Token stored in `.env` as `META_ACCESS_TOKEN` (never committed to git)

## Ad Account

- [ ] Ad Account ID confirmed (format: `act_XXXXXXXXXX`)
- [ ] Ad Account ID stored in `.env` as `META_AD_ACCOUNT_ID`
- [ ] Account is active (not disabled or restricted)

## Meta Pixel / CAPI (for conversion reporting)

- [ ] Pixel ID identified and accessible via the API
- [ ] Pixel is firing on key pages (purchase confirmation, lead form submission)
- [ ] CAPI is set up (recommended) or pixel-only is accepted

## Environment

- [ ] `.env` file created from `.env.example`
- [ ] Python 3.11+ installed
- [ ] Dependencies installed (`pip install -r requirements.txt`)
- [ ] API connectivity verified: `python src/check_access.py`

## Rate Limits (know before you run)

- Meta Marketing API has per-account and per-app rate limits.
- Default: ~200 API calls per hour per ad account for basic reads.
- Avoid running fetch scripts more than once per hour in production.
- Use batch requests where possible to reduce call count.
