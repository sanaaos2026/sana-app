{pkgs}: {
  deps = [
    pkgs.libgbm
    pkgs.alsa-lib
    pkgs.systemd
    pkgs.libxkbcommon
    pkgs.expat
    pkgs.mesa
    pkgs.xorg.libXrandr
    pkgs.xorg.libXfixes
    pkgs.xorg.libXdamage
    pkgs.xorg.libXcomposite
    pkgs.dbus
    pkgs.at-spi2-atk
    pkgs.atk
    pkgs.nss
    pkgs.nspr
    pkgs.pango
  ];
}
