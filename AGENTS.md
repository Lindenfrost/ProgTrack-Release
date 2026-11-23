# Repository working rules

## Release packages

- Save final Windows and Linux release packages in `Q:\GitHub\Release Packages`.
- The folder holds only release-ready package archives and their checksum sidecars. Each archive must contain the complete application payload and the intended seed data.
- Use the exact archive from this folder for manual testing and then publish that same file as the GitHub release artifact. Do not rebuild between acceptance and publication; if the package changes, retest the replacement archive.
- Keep extracted packages, staging directories, payload archives, build logs, caches, and other intermediate files in platform build directories or temporary storage, never in `Q:\GitHub\Release Packages`.
- Keep Windows and Linux packages as separate platform artifacts.
