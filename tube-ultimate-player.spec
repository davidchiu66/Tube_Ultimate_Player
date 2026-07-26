Name:           tube-ultimate-player
Version:        0.2.19
Release:        1%{?dist}
Summary:        YouTube and Bilibili desktop video player

License:        MIT
URL:            https://github.com/davidchiu66/Tube_Ultimate_Player
VCS:            {{{ git_dir_vcs }}}
Source0:        {{{ git_dir_pack }}}

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
{{{ git_dir_setup_macro }}}

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
    's|^Exec=.*|Exec=tube-ultimate-player %U|' \
*   %{buildroot}%{_datadir}/applica*ions/tube-ultimate-player.desktop
*install -m 0644 \
    docs/assets/*cons/app-icon-256.png \
    %{buil*root}%{_datadir}/icons/hicolor/256*256/apps/%{name}.png

desktop-file*validate \
    %{buildroot}%{_data*ir}/applications/tube-ultimate-pla*er.desktop

%files
%license LICENS*
%doc README.md
%doc docs/linux_bu*ld_and_release.md
%{_bindir}/tube-*ltimate-player
%{_datadir}/applica*ions/tube-ultimate-player.desktop
*{_datadir}/icons/hicolor/256x256/a*ps/%{name}.png
%{_datadir}/%{name}*

%changelog
* Sat Jul 25 2026 davidchiu66 <chinamen@gmail.com> - 0.2.19-1
- Drop py7zr from Fedora RPM runtime dependencies.

* Fri Jul 24 2026 davidchiu66 <chinamen@gmail.com> - 0.2.18-1
- Add Fedora RPM packaging for COPR builds.