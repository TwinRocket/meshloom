---
title: Go deeper
description: Transports, other install paths, variables, Community, short links, fanout, notifications, Home Assistant, troubleshooting.
level: deep
order: 10
---

This section assumes Meshloom is already running. It covers the settings and situations that the first pages leave out, so you do not need to open the code repository to find a setting.

It uses the project's own words: transport (how the computer talks to the radio), fanout (sending what Meshloom hears elsewhere), flood scope (the region a message is tagged with), path hash mode (how many bytes identify each repeater on a route) and secure context (a page served over HTTPS or from `localhost`). Each page explains the ones it needs. If one is still unclear, start with [What is it](/en/docs/what/) and [Around the messages](/en/docs/around/).

Two pages are worth reading before the others:

- [Variables and settings](/en/docs/deep/environment/) separates what is decided at startup from what lives in the database and is changed in the interface.
- [Security](/en/docs/deep/security/) says what Meshloom does not protect.

To be told when something happens, read [Notifications](/en/docs/deep/notifications/) (channels, events and rules; ntfy, Telegram, Discord, Home Assistant, e-mail, webhooks; the webhook body: [Notification webhook](/en/docs/deep/notifications-webhook/)) and, for browsers, [Push notifications](/en/docs/deep/push/). To forward every message to another system, read [Fanout](/en/docs/deep/fanout/).

[Meshloom Community](/en/docs/deep/community/) is off until you join it, so read its page to know what it sends before you do. The same goes for [Short links and photos](/en/docs/deep/share/), the optional sharing service that shortens long web addresses: it is off until an administrator turns it on.
