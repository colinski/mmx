# Copyright (c) OpenMMLab. All rights reserved.
import os.path as osp
import random
from abc import ABCMeta, abstractmethod

import numpy as np
from addict import Dict
from mmcv.utils import print_log
from mmdet.datasets.pipelines import Compose
from torch.utils.data import Dataset

from mmtrack.core.evaluation import eval_sot_ope
from mmtrack.datasets import DATASETS
import glob
import pickle
import cv2
import h5py
import torch
import json
import time
import torchaudio


def read_hdf5(f):
    data = {}
    for ms in f.keys():
        data[ms] = {}
        for k, v in f[ms].items():
            if k == 'mocap': #mocap or node_N
                data[ms]['mocap'] = v[()]
            else:
                data[ms][k] = {}
                for k2, v2 in f[ms][k].items():
                    if k2 == 'detected_points':
                        data[ms][k][k2] = v2[()]
                    else:
                        data[ms][k][k2] = v2[:]
    return data


@DATASETS.register_module()
class HDF5Dataset(Dataset, metaclass=ABCMeta):
    CLASSES = None
    def __init__(self,
                 hdf5_fname,
                 fps=20,
                 valid_keys=['mocap', 'zed_camera_left', 'zed_camera_depth'],
                 img_pipeline=[],
                 depth_pipeline=[],
                 azimuth_pipeline=[],
                 range_pipeline=[],
                 audio_pipeline=[],
                 test_mode=False,
                 **kwargs):
        self.class2idx = {'truck': 1, 'node': 0}
        # self.f = h5py.File(hdf5_fname, 'r')
        self.fname = hdf5_fname
        self.fps = fps
        
        with h5py.File(self.fname, 'r') as f:
            self.data = read_hdf5(f)
            
        self.keys = list(self.data.keys())
        self.keys = np.array(self.keys)
        sort_idx = np.argsort(self.keys)
        self.keys = self.keys[sort_idx]

        self.timesteps = torch.from_numpy(self.keys.astype(int))
        
        self.img_pipeline = Compose(img_pipeline)
        self.depth_pipeline = Compose(depth_pipeline)
        self.range_pipeline = Compose(range_pipeline)
        self.azimuth_pipeline = Compose(azimuth_pipeline)

        # if self.azimuth_pipeline is not None:
            # self.azimuth_pipeline = Compose(self.azimuth_pipeline)
        self.test_mode = test_mode
        
        self.start_time = int(self.keys[0]) #+ (60*60*1000)
        self.end_time = int(self.keys[-1])
        elapsed_time = self.end_time - self.start_time
        self.num_frames = int(elapsed_time / 1000) * self.fps
        self.frame_len = int((1 / self.fps) * 1000)
        self.buffer = {}
    
        self.flag = np.zeros(len(self), dtype=np.uint8) #ones?

        self.valid_keys = valid_keys
    

    def __len__(self):
        return self.num_frames

    def parse_buffer(self):
        for key, val in self.buffer.items():
            if key == 'mocap':
                mocap_data = json.loads(val)
                positions = [d['normalized_position'] for d in mocap_data]
                labels = [self.class2idx[d['type']] for d in mocap_data]
                ids = [d['id'] for d in mocap_data]
                self.buffer[key] = {
                    'gt_positions': torch.tensor(positions),
                    'gt_labels': torch.tensor(labels).long(),
                    'gt_ids': torch.tensor(ids).long()
                }

            if key == 'zed_camera_left':
                img = cv2.imdecode(val, 1)
                self.buffer[key] = self.img_pipeline(img)
        
            if key == 'zed_camera_right':
                img = cv2.imdecode(val, 1)
                self.buffer[key] = self.img_pipeline(img)
        
            if key == 'zed_camera_depth':
                self.buffer[key] = self.depth_pipeline(val)
            
            if key == 'realsense_camera_img':
                img = cv2.imdecode(val, 1)
                self.buffer[key] = self.img_pipeline(img)

            if key == 'realsense_camera_depth':
                img = cv2.imdecode(val, 1)
                self.buffer[key] = self.img_pipeline(img)

            if key == 'azimuth_static':
                arr = np.nan_to_num(val)
                self.buffer[key] = self.azimuth_pipeline(arr)
                    
            if key == 'range_doppler':
                self.buffer[key] = self.range_pipeline(val.T)
            
            # if key == 'mic_waveform':
                # wave = val[:]
                # wave = torch.from_numpy(wave.T)
                # spectro = torchaudio.transforms.Spectrogram()(wave)
                # C, H, W = spectro.shape
                # spectro = spectro.reshape(C*H, W)
                # self.buffer[key] = spectro


    def fill_buffer(self, start_idx, end_time):
        start_idx = 0 
        for time in self.keys[start_idx:]:
            data = self.data[time]
            if 'mocap' in data.keys():
                self.buffer['mocap'] = data['mocap']
            if 'node_1' in data.keys():
                data = data['node_1']

            for key, val in data.items():
                self.buffer[key] = val
            
            if int(time) >= end_time:
                return
        
    def __getitem__(self, ind):
        start = int(self.start_time + ind * self.frame_len) #start is N frames after start_time
        diffs = torch.abs(self.timesteps - start) #find closest time as it isnt frame perfect
        min_idx = torch.argmin(diffs).item()
        end = int(self.timesteps[min_idx] + self.frame_len) #one frame worth of data
        self.fill_buffer(min_idx, end) #run through data and fill buffer
        self.parse_buffer() #convert to arrays
        data = {k: v for k, v in self.buffer.items() if k in self.valid_keys}
        data['ind'] = ind
        return data

        # img = self.img_pipeline(data['zed']['left'])
        # depth = self.depth_pipeline(data['zed']['depth'])
        # azimuth = self.azimuth_pipeline(data['mmwave']['azimuth_static'])
        # drange = self.range_pipeline(data['mmwave']['range_doppler'])


    def evaluate(self, outputs, **eval_kwargs):
        pred_pos = np.array(outputs['pred_position'])
        assert pred_pos.shape[1] == 1
        pred_pos = pred_pos.squeeze()
        gt_pos = np.array(outputs['gt_position'])
        sq_err = (pred_pos - gt_pos)**2
        return {'mse': sq_err.mean()}
