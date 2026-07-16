The arm64 image installs `flask` in the obs-server venv, matching the amd64 image — obs-server would otherwise crash-loop on arm64 (it imports flask but the venv lacked it).
