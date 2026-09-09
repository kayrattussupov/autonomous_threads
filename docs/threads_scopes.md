# Threads API scope check

Generated: 2026-09-09T08:31:30.907884+00:00

`debug_token` on `graph.facebook.com` fails consistently (not transient — same
result on two separate runs 30 minutes apart):

`debug_token` request failed: HTTP 400 — {"error":{"message":"Error validating application. Cannot get application info due to a system error.","type":"OAuthException","code":190}}

This is an app-level introspection issue on Meta's side, unrelated to which
scopes are granted — `code 190` here means Meta couldn't load the *app's*
metadata, not that the token/scopes are invalid.

## Verified instead via live calls to graph.threads.net

Each scope was confirmed by actually exercising the endpoint it gates,
2026-09-09:

- [x] `threads_basic` — `GET /me` returned the profile (`kayrat_labs`)
- [x] `threads_content_publish` — `GET /{user_id}/threads_publishing_limit`
      returned real quota data (`250/day`, `1` used)
- [x] `threads_manage_insights` — `GET /{media_id}/insights` returned
      views/likes/replies for a real post
- [x] `threads_manage_replies` — `GET /{media_id}/replies` returned successfully
      (empty list — that post has no replies yet, but no permission error)

All required scopes confirmed granted. T0.1 acceptance criterion met.

## Required scopes
- [x] `threads_basic`
- [x] `threads_content_publish`
- [x] `threads_manage_insights`
- [x] `threads_manage_replies`
