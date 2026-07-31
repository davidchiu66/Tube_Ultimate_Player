Name:           tube-ultimate-player
Version:        0.2.21
Release:        1%{?dist}
Summary:        YouTube and Bilibili desktop video player

License:        MIT
URL:            https://github.com/davidchiu66/Tube_Ultimate_Player
VCS:            git:https://github.com/davidchiu66/Tube_Ultimate_Player.git
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch

BuildRequires:  desktop-file-utils
BuildRequires:  python3

Requires:       python3
Requires:       python3dist(pyside6)
Requires:       python3dist(qtawesome)
Requires:       ffmpeg
Requires:       mpv-libs
Requires:       yt-dlp

%description
Tube Ultimate Player is a desktop video player for YouTube and Bilibili,
built with Python, PySide6, yt-dlp, and libmpv.

%prep
%autosetup -n %{name}-%{version}

%build
# This application contains interpreted Python source files and does not
# require a compilation step.

%install
install -d %{buildroot}%{_datadir}/%{name}
install -d %{buildroot}%{_bindir}
install -d %{buildroot}%{_datadir}/applications
install -d %{buildroot}%{_datadir}/icons/hicolor/256x256/apps

cp -a \
    app_paths.py \
    main.py \
    platform_support.py \
    config \
    database \
    dlna \
    docs \
    download \
    player \
    resolver \
    resources \
    services \
    ui \
    workers \
    app_version.txt \
    THIRD_PARTY_NOTICES.md \
    %{buildroot}%{_datadir}/%{name}/

install -m 0755 \
    packaging/rpm/tube-ultimate-player \
    %{buildroot}%{_bindir}/tube-ultimate-player

install -m 0644 \
    packaging/linux/tube-ultimate-player.desktop \
    %{buildroot}%{_datadir}/applications/tube-ultimate-player.desktop

sed -i \
    's|^Exec=.*|Exec=tube-ultimate-player %%U|' \
    %{buildroot}%{_datadir}/applications/tube-ultimate-player.desktop

install -m 0644 \
    docs/assets/icons/app-icon-256.png \
    %{buildroot}%{_datadir}/icons/hicolor/256x256/apps/%{name}.png
    's|^Exec=.*|Exec=tube-ultimate-player %%U|' \
    %{buildroot}%{_datadir}/applications/tube-ultimate-player.desktop

install -m 0644 \
    docs/assets/icons/app-icon-256.png \
    %{buildroot}%{_datadir}/icons/hicolor/256x256/apps/%{name}.png

desktop-file-validate \
    %{buildroot}%{_datadir}/applications/tube-ultimate-player.desktop
desktop-file-validate \
    %{buildroot}%{_datadir}/applications/tube-ultimate-player.desktop

%files
%license LICENSE
%license LICENSE
%doc README.md
%doc docs/linux_build_and_release.md
%{_bindir}/tube-ultimate-player
%{_datadir}/applications/tube-ultimate-player.desktop
%{_datadir}/icons/hicolor/256x256/apps/%{name}.png
%{_datadir}/%{name}/
%doc docs/linux_build_and_release.md
%{_bindir}/tube-ultimate-player
%{_datadir}/applications/tube-ultimate-player.desktop
%{_datadir}/icons/hicolor/256x256/apps/%{name}.png
%{_datadir}/%{name}/

%changelog
* Fri Jul 31 2026 davidchiu66 <chinamen@gmail.com> - 0.2.21-1
- Verify update packages, harden archive extraction and DLNA relay access.
- Fix the online upgrade launcher and improve home page and startup performance.

* Sun Jul 26 2026 davidchiu66 <chinamen@gmail.com> - 0.2.19-2
- Escape the desktop file field code in the RPM spec.

* Sun Jul 26 2026 davidchiu66 <chinamen@gmail.com> - 0.2.19-2
- Escape the desktop file field code in the RPM spec.

* Sat Jul 25 2026 davidchiu66 <chinamen@gmail.com> - 0.2.19-1
- Drop py7zr from Fedora RPM runtime dependencies.

* Fri Jul 24 2026 davidchiu66 <chinamen@gmail.com> - 0.2.18-1
- Add Fedora RPM packaging for COPR builds.
