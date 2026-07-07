# Third-Party Notices

This project depends on, bundles, or integrates with the following third-party software.

## PySide6 / Qt for Python

- Project: PySide6
- Website: https://doc.qt.io/qtforpython/
- Copyright: The Qt Company and contributors
- License: Refer to the official Qt for Python licensing documentation

## yt-dlp

- Project: yt-dlp
- Website: https://github.com/yt-dlp/yt-dlp
- Copyright: yt-dlp contributors
- License: Refer to the upstream repository and release artifacts
- Usage in this project: external command-line executable invoked by the application and CI workflows

## mpv / libmpv

- Project: mpv
- Website: https://mpv.io/
- Copyright: mpv authors and contributors
- License: Refer to the upstream project distribution for the exact bundled binary license terms
- Usage in this project: native playback library loaded by the application

## Additional Binary Dependencies

The `3rdpart/` directory may contain runtime binary dependencies required by bundled playback components,
such as graphics or media support libraries. Their copyright and licensing remain with their
respective upstream authors.

## Redistribution Reminder

If you publish binaries or installers built from this repository:

1. Review the licenses of every bundled third-party file in `3rdpart/`.
2. Include upstream license texts or links where required.
3. Do not remove or alter third-party copyright notices.
4. Ensure your redistribution complies with the original licenses of yt-dlp, mpv/libmpv, Qt/PySide6, and any other bundled binaries.
