from mmtrack.datasets import build_dataloader, build_dataset

img_norm_cfg = dict(mean=[123.675, 116.28, 103.53], std=[58.395, 57.12, 57.375], to_rgb=True)
img_pipeline = [
    dict(type='DecodeJPEG'),
    dict(type='LoadFromNumpyArray'),
    dict(type='Resize', img_scale=(270, 480), keep_ratio=True),
    dict(type='RandomFlip', flip_ratio=0.0),
    dict(type='Normalize', **img_norm_cfg),
    dict(type='DefaultFormatBundle'),
    dict(type='Collect', keys=['img']),
]

r50_pipeline = [
    dict(type='LoadFromNumpyArray', force_float32=True),
    dict(type='RandomFlip', flip_ratio=0.0),
    dict(type='DefaultFormatBundle'),
    dict(type='Collect', keys=['img']),
]

depth_pipeline = [
    dict(type='LoadFromNumpyArray', force_float32=True),
    dict(type='RandomFlip', flip_ratio=0.0),
    dict(type='Normalize', mean=[0], std=[20000], to_rgb=False),
    dict(type='Normalize', mean=[1], std=[0.5], to_rgb=False),
    dict(type='DefaultFormatBundle'),
    dict(type='Collect', keys=['img']),
]

azimuth_pipeline = [
    dict(type='LoadFromNumpyArray', force_float32=True),
    dict(type='RandomFlip', flip_ratio=0.0),
    dict(type='DefaultFormatBundle'),
    dict(type='Collect', keys=['img']),
]

range_pipeline = [
    dict(type='LoadFromNumpyArray', force_float32=True, transpose=True),
    dict(type='Resize', img_scale=(256, 16), keep_ratio=True),
    dict(type='RandomFlip', flip_ratio=0.0),
    dict(type='Normalize', mean=[4353], std=[705], to_rgb=False),
    dict(type='DefaultFormatBundle'),
    dict(type='Collect', keys=['img']),
]

audio_pipeline = [
    dict(type='LoadFromNumpyArray', force_float32=True, transpose=True),
    dict(type='RandomFlip', flip_ratio=0.0),
    dict(type='DefaultFormatBundle'),
    dict(type='Collect', keys=['img']),
]

pipelines = {
    'zed_camera_left_r50': r50_pipeline,
    'realsense_camera_r50': r50_pipeline,
    'zed_camera_left': img_pipeline,
    'zed_camera_depth': depth_pipeline,
    'azimuth_static': azimuth_pipeline,
    'range_doppler': range_pipeline,
    'mic_waveform': audio_pipeline,
    'realsense_camera_img': img_pipeline,
    'realsense_camera_depth': img_pipeline
} 

#possible valid mods
# valid_mods=['mocap', 'zed_camera_left', 'zed_camera_depth',
            # 'range_doppler', 'azimuth_static', 'mic_waveform',
            # 'realsense_camera_depth', 'realsense_camera_img']

valid_mods=['mocap', 'zed_camera_left', 'mic_waveform']

valid_nodes=[1,2,3,4]

data_root = 'data/mmm/2022-09-01/trucks2_lightsT_obstaclesF/train'
trainset=dict(type='HDF5Dataset',
    cacher_cfg=dict(type='DataCacher',
        hdf5_fnames=[
            f'{data_root}/mocap.hdf5',
            f'{data_root}/node_1/mmwave.hdf5',
            f'{data_root}/node_2/mmwave.hdf5',
            f'{data_root}/node_3/mmwave.hdf5',
            f'{data_root}/node_4/mmwave.hdf5',
            f'{data_root}/node_1/realsense.hdf5',
            f'{data_root}/node_2/realsense.hdf5',
            f'{data_root}/node_3/realsense.hdf5',
            f'{data_root}/node_4/realsense.hdf5',
            f'{data_root}/node_1/respeaker.hdf5',
            f'{data_root}/node_2/respeaker.hdf5',
            f'{data_root}/node_3/respeaker.hdf5',
            f'{data_root}/node_4/respeaker.hdf5',
            f'{data_root}/node_1/zed.hdf5',
            f'{data_root}/node_2/zed.hdf5',
            f'{data_root}/node_3/zed.hdf5',
            f'{data_root}/node_4/zed.hdf5',
        ],
        valid_mods=valid_mods,
        valid_nodes=valid_nodes,
        include_z=False, #(x,y) position only
    ),
    num_future_frames=0,
    num_past_frames=4, #sequence of length 5
    pipelines=pipelines,
)


dataset = build_dataset(trainset)

import ipdb; ipdb.set_trace() # noqa
data_loader = build_dataloader(
    dataset,
    samples_per_gpu=2, #batch_size per gpu
    workers_per_gpu=1, #workers per gu
    num_gpus=1,
    samples_per_epoch=None,
    dist=True,
    shuffle=True,
    seed=42,
    persistent_workers=False
)

dataset.write_video(outputs=None, logdir='test', video_length=100)

#seq has length 5
for seq in data_loader:
    for batch in seq:
        #img is B x 3 x H x W
        img = batch[('zed_camera_left', 'node_1')]['img'].data[0]
        gt = batch[('mocap', 'mocap')]

        #gt_pos is B x num_objs x 2 
        gt_pos = gt['gt_positions']
        import ipdb; ipdb.set_trace() # noqa
