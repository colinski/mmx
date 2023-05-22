#DIR=$(dirname "$(readlink -f "$0")") 
DIR=$1

#bash $DIR/data.sh
#EXPDIR=$DIR/log
#if [ -d $EXPDIR ]; then
    #rm -rf $EXPDIR/*
#fi
#singularity run --nv -H $WORK $WORK/sif/python.sif $WORK/src/mmtracking/tools/dist_train.sh $DIR/config.py $2 --work-dir $EXPDIR --seed 5
#rm $DIR/log/config.py
singularity run --nv $WORK/sif/python.sif python $WORK/src/mmtracking/tools/tune.py $DIR
singularity run --nv $WORK/sif/python.sif python $WORK/src/mmtracking/tools/test_from_tune.py $DIR --write_video --save_outputs
