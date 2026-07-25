#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

set -e
export DFTRACER_ENABLE=1
export DFTRACER_INC_METADATA=1
export DFTRACER_LOG_FILE=${WS}/val_opt3_batch64/traces/raw/val_opt3_batch64
export DFTRACER_DATA_DIR=all
export DFTRACER_INIT=FUNCTION
export DFTRACER_ENABLE_HDF5=0
cd ${WS}/annotated/source/training
source /usr/share/lmod/lmod/init/bash && module load craype-x86-trento libfabric/match_SHS craype-network-ofi perftools-base/25.09.0 craype/2.7.35 PrgEnv-cray/8.7.0 flux_wrappers/0.1 xpmem/2.6.5 cce/20.0.0 cray-libsci/25.09.0 cray-mpich/9.0.1 python/3.13.2 rocm/6.3.1 && export LD_LIBRARY_PATH="/opt/cray/pe/cce/20.0.0/cce/x86_64/lib:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib/default64:/usr/lib64:${LD_LIBRARY_PATH}" && source ${WS}/install/bin/activate && export LD_LIBRARY_PATH="${WS}/install/lib/python3.13/site-packages/torch/lib:${LD_LIBRARY_PATH}" && export HF_DATASETS_CACHE="${WS}/tmp/hf_datasets_cache_opt3" HF_MODULES_CACHE="${WS}/tmp/hf_modules_cache_opt3" HF_HOME="${WS}/tmp/hf_home_opt3" MIOPEN_FIND_MODE=fast MIOPEN_USER_DB_PATH="${WS}/tmp/miopen_cache" MIOPEN_CUSTOM_CACHE_DIR="${WS}/tmp/miopen_cache" HSA_ENABLE_SDMA=0 && time python train_pubchem_light.py --n_head 4 --n_layer 4 --n_embd 128 --fc_h 256 --n_batch 64 --num_epoch 1 --max_len 128 --train_load pubchem --num_nodes 1 --device cuda --n_workers 2 --accelerator ddp --n_jobs 1 --gpus 4
