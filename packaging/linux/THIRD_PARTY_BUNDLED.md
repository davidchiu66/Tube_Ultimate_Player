# Linux bundled components

The enhanced Linux packages bundle the following runtime components:

- libmpv and the user-space shared-library dependency closure collected by linuxdeploy
- FFmpeg and FFprobe
- Deno
- yt-dlp
- PySide6/Qt and Python dependencies collected by PyInstaller

Every release build must include `third-party-manifest.sha256`, `bundled-runtime-manifest.sha256` and the license/copyright files placed under `licenses/`. The build workflow records the exact downloaded artifacts, collected shared libraries and SHA256 values.

Redistribution must follow the licenses that apply to the exact binaries used by the build. In particular, FFmpeg may be LGPL or GPL depending on its build configuration. Merely including this notice does not replace the corresponding license text, copyright information, source-code offer or other obligations required by those licenses.
