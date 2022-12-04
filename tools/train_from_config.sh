#!/usr/bin/env bash

CONFIG=$1
NAME=$(basename $1 .py)
EXPDIR=logs/$NAME
singularity run --nv -H $WORK $WORK/sif/python.sif $WORK/src/mmtracking/tools/dist_train.sh $CONFIG $2 --work-dir $EXPDIR/log --seed 5
