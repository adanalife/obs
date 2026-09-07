Build the arm64 base image with `-DENABLE_VLC=OFF`, so dropping the `libvlc-dev` build dependency no longer fails OBS's CMake configure step.
