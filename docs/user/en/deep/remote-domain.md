---
title: Your own domain for Meshloom Remote
description: Make your own domain name lead to your Meshloom Remote address with a URL redirect, and check it from Meshloom with the Test button.
level: deep
order: 24.4
---

Meshloom Remote gives your instance an address such as `https://k3xwq2m7ta.meshloom.link`. If you own a domain (`chat.example.org`), you can make it lead there at no cost to you or to TwinRocket, with a **URL redirect** set at the place that manages your domain. This page explains how, what to expect and how to test it.

## Why a redirect and not a CNAME

You cannot point your domain at the relay with a `CNAME` record: the relay sits behind Cloudflare, which refuses a name that belongs to another account (error 1014, “CNAME Cross-User Banned”). Making that work would need a paid Cloudflare option, which Meshloom Remote does not use. A redirect needs nothing from us: your domain answers “go to `https://<your address>`” and the browser follows.

## What it looks like

- Someone types `chat.example.org`. Their browser is sent to your Meshloom Remote address and **the address bar then shows `<name>.meshloom.link`**, not your domain. That is the limit of this method.
- Every device still has to be allowed (by QR code or access request), exactly as with the direct address ([Accounts and access](/en/docs/deep/access/)): the redirect gives no access by itself.
- If you replace the Remote key in Settings → Remote access, the instance gets a new address. Update the redirect, and use a temporary redirect (HTTP 302 or 307) rather than a permanent one: browsers remember a permanent one and keep sending people to the old address.
- If you want your own domain to stay in the address bar, Meshloom Remote cannot do it. Use one of the paths in [Remote access on your own](/en/docs/deep/remote-self-hosted/) (a Cloudflare Tunnel or a reverse proxy that you run).

## Set the redirect

First copy your address from **Settings → Remote access → Status** (the line starting with `https://`). Then create a redirect from your domain to it. Pick the case that fits.

### At your registrar or DNS host

Most registrars call it “URL redirect”, “web forwarding” or “domain forwarding”.

1. Open the domain's redirect or forwarding settings.
2. Source: `chat.example.org` (or the bare domain).
3. Destination: your Meshloom Remote address, `https://<name>.meshloom.link`.
4. Type: temporary (302 or 307). Do not choose “masked”, “frame” or “cloaking”: that loads Meshloom inside a frame, which does not work.
5. If the registrar offers it, keep the path.

### In Cloudflare (your own account)

1. Make sure the name is in your zone and **proxied** (orange cloud). If it has no real target, add a placeholder record such as an `A` record to `192.0.2.1`; the redirect is answered before it is used.
2. Rules → Redirect Rules → Create rule. When: Hostname equals `chat.example.org`.
3. Then: Dynamic or Static redirect to `https://<name>.meshloom.link`, status 302.
4. Save and wait a minute.

## Test it from Meshloom

As an administrator, open **Settings → Remote access**. Meshloom Remote needs Meshloom Community on (see [Features that need Community](/en/docs/deep/community/#features-that-need-community)). Once Meshloom Remote is on and registered, the **Your own domain** group appears. Type your domain and press **Test**. Your instance sends one request to that domain (it tries HTTPS, then HTTP if HTTPS fails) and does not follow the redirect. It never contacts the relay for this. The result is one of:

| Result | Meaning | What to do |
|---|---|---|
| The redirect works | The domain answers with a redirect to your address. | Nothing. Try it from a phone, outside your network. |
| No redirect | The domain answers, but without a redirect (for example a parking page). | Create the redirect above, or wait for the registrar to apply it. |
| Wrong target | The domain redirects somewhere else, shown in the message. | Fix the destination: it must be exactly your `<name>.meshloom.link`. |
| Not a domain name | What you typed is not a name such as `chat.example.org`. | Remove the port, the path or the IP address. |
| Your domain could not be reached | The name does not resolve, answers too slowly, has a bad certificate or points to a private address. | Check the DNS record and the certificate; the reason is shown. |

A domain that points to your own network (a private or local address) is never contacted by this test, as for every address Meshloom is asked to call. The test runs from your instance, so it sees what your instance sees; a registrar change can take a while to reach every resolver.

The domain you type is not saved anywhere. Web Push notifications therefore use your `<name>.meshloom.link` address as their contact address when none is set, never your own domain (see [Push notifications](/en/docs/deep/push/#the-contact-address-vapid)).
