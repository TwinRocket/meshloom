# Test scripts (manual, not part of the gate)

- `upgrade_matrix/`: the upgrade matrix to run before each rollout step (`MATRIX.txt`).
- `plugin_mutations.py`: removes the plugin sandbox guards one at a time and checks that the
  hostile corpus or the RPC fuzz catches each (`docs/dev/plugins-security.md`, "Tests").
- `meshloom_admin_tty.sh`: checks that `meshloom-admin` never gives the `meshloom` child root's
  controlling terminal, piped or not (run as root; CI runs it in `.github/workflows/nfpm.yml`).
