---
title: Go deeper
description: Transports, other install paths, variables, Community, fanout, Home Assistant, troubleshooting.
level: deep
order: 10
---

This section assumes Meshloom is already running. It covers the settings and situations that the first pages leave out, so you do not need to open the code repository to find a setting.

It uses the project's own words: transport (how the computer talks to the radio), fanout (sending what Meshloom hears elsewhere), flood scope (the region a message is tagged with), path hash mode (how many bytes identify each repeater on a route) and secure context (a page served over HTTPS or from `localhost`). Each page explains the ones it needs. If one is still unclear, start with [What is it](/en/docs/what/) and [Around the messages](/en/docs/around/).

Two pages are worth reading before the others:

- [Variables and settings](/en/docs/deep/environment/) separates what is decided at startup from what lives in the database and is changed in the interface.
- [Security](/en/docs/deep/security/) says what Meshloom does not protect.

[Meshloom Community](/en/docs/deep/community/) is on for new installations unless you opt out, so read its page to know what it sends.
