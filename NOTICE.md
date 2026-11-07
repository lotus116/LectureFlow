# LectureFlow licensing scope

Copyright (C) 2026 LectureFlow contributors.

LectureFlow's original application source code, build and test scripts, and original
project documentation are licensed under the GNU General Public License, version 3
only (`GPL-3.0-only`), unless a file states otherwise. See [LICENSE](LICENSE).
This choice does not grant an option to use an unspecified future GPL version.

You may use and modify the covered software, including commercially. If you convey
covered modified software, provide recipients the corresponding source and the GPL
rights and notices required by the license. Private modifications do not have to be
published. Ordinary network use alone does not trigger an AGPL-style source offer.
No warranty is provided. The full license, not this summary, controls.

## Separate rights and licenses

The GPL grant covers only rights held by LectureFlow's contributors. It does not
relicense dependencies, pretrained model weights, copied third-party notices,
operating-system resources, trademarks, or other people's work.

- Dependencies and model weights retain their own licenses and notices. Bundled
  FFmpeg is a separate GPL program; its exact binary and corresponding-source
  obligations must be checked separately from the BSD-licensed imageio wrapper.
- The Cirno/Touhou/fumo-themed `docs/assets/logo.png`, `hero.png`,
  `hero-notebook.png`, and `lecture-review-story.jpg` are **not offered under this
  GPL grant**. Underlying character, plush-design, and reference-photo rights are
  not established by AI-assisted generation or by this repository's license.
- The promotional video linked in README and its music are **not offered under
  this GPL grant**. Attribution is not a license or proof of permission.
- Screenshots in `docs/assets/ui-redesign/`, including historical copies, may
  contain third-party UI/system artwork. No license to those underlying rights
  is granted here. System fonts and SF Symbols remain subject to their owners'
  terms; this repository does not redistribute a font or symbol library.
- Original editable diagrams `course-sharing.svg`, `features-notebook.svg`, and
  `workflow.svg` are covered by the project GPL grant to the extent of the
  contributors' own rights.
- Historical copies of an asset retain the same exclusions. Preserved historical
  documentation may describe an earlier licensing assumption; the current audit
  is authoritative about what has actually been verified.

These exclusions do not clear the continued use of a third-party asset. Unresolved
permissions and binary-release blockers are listed in
[the licensing audit](docs/licenses/AUDIT-2026-11-07.md). Adding this file does not
retroactively repair already published installation packages or grant rights the
contributors do not hold.

## Source and contributions

Application source and build instructions: <https://github.com/lotus116/LectureFlow>.
For a distributed binary, the exact commit and corresponding source must accompany
its release; a moving `main` link alone is not a complete source delivery.
See [the release checklist](docs/licenses/RELEASE-CHECKLIST.md).

The reviewed mainline history uses two author names with the same contributor
identity. External PR #6 was still open and unmerged at audit time. History is
supporting provenance evidence, not proof that every underlying work is owned.
Future contributions follow [CONTRIBUTING.md](CONTRIBUTING.md); an open PR made
before this license was added is not silently relicensed or approved for merge.
