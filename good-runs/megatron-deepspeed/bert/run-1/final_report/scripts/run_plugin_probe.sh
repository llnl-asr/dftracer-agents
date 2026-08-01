#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

WS="${WS}"
source "$WS/scripts/env.sh" >/dev/null 2>&1
export LD_LIBRARY_PATH="/collab/usr/global/tools/rccl/toss_4_x86_64_ib_cray/rocm-6.4.1/install/lib:$LD_LIBRARY_PATH"
export NCCL_DEBUG=INFO
export NCCL_DEBUG_SUBSYS=INIT,NET
export RANK=$FLUX_TASK_RANK WORLD_SIZE=4 LOCAL_RANK=0
export MASTER_ADDR=$1 MASTER_PORT=29931
python3 -c "
import os,torch,torch.distributed as dist
torch.cuda.set_device(0)
dist.init_process_group('nccl',rank=int(os.environ['RANK']),world_size=4)
t=torch.ones(4,device='cuda')*int(os.environ['RANK']); dist.all_reduce(t)
if int(os.environ['RANK'])==0: print('ALLREDUCE OK ->',t.tolist())
dist.destroy_process_group()"
