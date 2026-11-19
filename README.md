# ERC Communities

This site was created for ERC Communities.

The source, design, and assets are proprietary. See [LICENSE](LICENSE). Do not copy them without permission.

Contact: [info@erccommunities.com](mailto:info@erccommunities.com)

## Chatwoot support widget (issue #7) — dormant

`snapshot/` is a static snapshot of the WordPress site (`pages_build_output_dir: ./snapshot`).
The widget is embedded there and **off by default**; nothing customer-visible loads until it is
switched on.

| Part | File |
|---|---|
| Loader | `snapshot/chatwoot.js` |
| Page config (launcher "Chat with ERC", colour `#2563eb`) | inline `window.CHATWOOT_CONFIG` before `</body>`; re-injected by `scripts/clean_snapshot.py` |
| Styles | `snapshot/chatwoot-polish.css` |
| Runtime switch | `functions/_middleware.js` |

Asset paths are root-relative (`/chatwoot.js`, `/chatwoot-polish.css`) because Cloudflare Pages
serves `snapshot/` as the site root — `/snapshot/chatwoot.js` does not exist.

### The snapshot regenerates

`scripts/snapshot_site.py` re-downloads the WordPress pages and `scripts/clean_snapshot.py`
cleans them. The cleaner re-adds the whole widget block when a page has no `/chatwoot.js`
reference, so regeneration keeps the widget dormant instead of dropping it.

### Turning it on / off

Cloudflare → Workers & Pages → `erccommunities` → Settings → Environment variables:

```text
CHATWOOT_ENABLED=1
CHATWOOT_WEBSITE_TOKEN=<token for "ERC Communities — Website", inbox 8>
CHATWOOT_BASE_URL=https://support.suncoastservers.com   # optional, this is the default
```

Set them on **Preview** first, check, then **Production**. Remove/unset `CHATWOOT_ENABLED` to go
dark again. No HTML edit, no token in git.

### Verify

```bash
curl -s https://erccommunities.pages.dev/ | grep -c 'websiteToken'   # 2 when on
curl -sI https://erccommunities.pages.dev/chatwoot.js | head -1      # 200 text/javascript
```

Browser: launcher → send a message → it must land in **ERC Communities — Website** with
`brand-erc` + `channel-widget`.

### Compliance note

ERC Communities covers Regulation Crowdfunding. The widget copy stays generic (a person answers,
AI is backup) with **no investment advice, no offer, no performance claims**. The brand's Help
Center articles are still drafts — keep the flag off until Ryan/legal publish them.

Note: the public WordPress site (`erccommunities.com`) and this snapshot deployment are separate
surfaces. The widget is wired here for the snapshot only; enabling it does not touch WordPress.
