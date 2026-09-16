# Meshloom add-on repository

Add this repository in Home Assistant under **Settings → Add-ons → Add-on store →
⋮ → Repositories**:

```
https://github.com/bagl3y/meshloom
```

Then install **Meshloom** from the store.

## What is here

- `meshloom/config.yaml` — the add-on manifest: options, their schema, the ports it
  publishes, and the hardware it asks for.
- `meshloom/run.sh` — reads the options Home Assistant writes and hands them to
  Meshloom as the environment variables it already understands. Home Assistant
  does not turn options into environment variables by itself, so this is the only
  glue the add-on needs.
- `meshloom/DOCS.md` — what a reader sees on the add-on's Documentation tab.

The image is the one published for every release, so installing the add-on pulls a
build rather than making one.
