#!/bin/bash
export LD_LIBRARY_PATH="$PROJECT_ROOT/workspaces/h5bench/20260710_061131/hdf5_1.14/lib:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib:/opt/cray/pe/cce/20.0.0/cce-clang/x86_64/lib:/usr/lib64:$LD_LIBRARY_PATH"
exec "$@"
