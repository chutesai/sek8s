### Removed

- The `changelog-auto-promote.yml` workflow. Changelog fragments are now promoted by hand
  with `make promote-changelogs` on the release branch; CI no longer commits to release
  branches. PRs to `main` are still refused while any fragment remains unpromoted.
