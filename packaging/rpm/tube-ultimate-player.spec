Name:           tube-ultimate-player
Version:        %{?version}%{!?version:0.0.0}
Release:        1%{?dist}
Summary:        YouTube and Bilibili desktop video player

License:        MIT
URL:            https://github.com/davidchiu66/Tube_Ultimate_Player
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  desktop-file-utils
BuildRequires:  python3-devel

Requires:       python3
Requires:       python3dist(pyside6)
Requires:       python3dist(qtawesome)
Requires:       python3dist(py7zr)
Requires:       ffmpeg
Requires:       mpv-libs
Requires:       yt-dlp

%description
Tube Ultimate Player is a desktop video player for YouTube and Bilibili,
built with Python, PySide6, yt-dlp and libmpv.

%prep
%autosetup

%build

%install
install -d %{buildroot}%{_datadir}/tube-ultimate-player
install -d %{buildroot}%{_bindir}
install -d %{buildroot}%{_datadir}/applications
install -d %{buildroot}%{_datadir}/icons/hicolor/256x256/apps
install -d %{buildroot}%{_licensedir}/%{name}
install -d %{buildroot}%{_docdir}/%{name}

cp -a \
  app_paths.py main.py platform_support.py \
  config database dlna docs download player resolver resources services ui workers \
  app_version.txt THIRD_PARTY_NOTICES.md \
  %{buildroot}%{_datadir}/tube-ultimate-player/

install -m 0755 packaging/rpm/tube-ultimate-player %{buildroot}%{_bindir}/tube-ultimate-player
install -m 0644 packaging/linux/tube-ultimate-player.desktop \
  %{buildroot}%{_datadir}/applications/tube-ultimate-player.desktop
sed -i 's/^Exec=.*/Exec=tube-ultimate-player %U/' \
  %{buildroot}%{_datadir}/applications/tube-ultimate-player.desktop
install -m 0644 docs/assets/icons/app-icon-256.png \
  %{buildroot}%{_datadir}/icons/hicolor/256x256/apps/tube-ultimate-player.png
install -m 0644 LICENSE %{buildroot}%{_licensedir}/%{name}/LICENSE
install -m 0644 README.md docs/linux_build_and_release.md \
  %{buildroot}%{_docdir}/%{name}/

desktop-file-validate %{buildroot}%{_datadir}/applications/tube-ultimate-player.desktop

%files
%license %{_licensedir}/%{name}/LICENSE
%doc %{_docdir}/%{name}/README.md
%doc %{_docdir}/%{name}/linux_build_and_release.md
%{_bindir}/tube-ultimate-player
%{_datadir}/applications/tube-ultimate-player.desktop
%{_datadir}/icons/hicolor/256x256/apps/tube-ultimate-player.png
%{_datadir}/tube-ultimate-player/

%changelog
* Fri Jul 24 2026 davidchiu66 <chinamen@gmail.com> - 0.2.18-1
- Add Fedora RPM packaging for COPR builds.
