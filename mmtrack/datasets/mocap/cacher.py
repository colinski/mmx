from abc import ABCMeta, abstractmethod
import os
import glob
import pickle
import numpy as np
from mmdet.datasets.pipelines import Compose
from torch.utils.data import Dataset
from mmtrack.datasets import DATASETS
import cv2
import h5py
import torch
import json
import time
import torchaudio
from tqdm import trange, tqdm
import matplotlib.pyplot as plt
import copy
import mmcv
from mmcv.runner import get_dist_info
from matplotlib.patches import Ellipse, Rectangle
from collections import defaultdict
import torch.distributions as D
from scipy.spatial import distance
from trackeval.metrics import CLEAR
import matplotlib
from .viz import init_fig, gen_rectange, gen_ellipse, rot2angle

def convert2dict(f, keys, fname, valid_mods, valid_nodes):
    data = {}
    for ms in tqdm(keys, desc='loading %s' % fname):
        data[ms] = {}
        for k, v in f[ms].items():
            if k == 'mocap': #mocap or node_N
                data[ms]['mocap'] = v[()]
            else: #is node_N
                if k not in valid_nodes:
                    continue
                data[ms][k] = {}
                for k2, v2 in f[ms][k].items():
                    if k2 not in valid_mods:
                        continue
                    if k2 == 'detected_points':
                        data[ms][k][k2] = v2[()]
                    else:
                        data[ms][k][k2] = v2[:]
    return data


def load_chunk(fname, valid_mods, valid_nodes):
    with h5py.File(fname, 'r') as f:
        keys = list(f.keys())
        keys = np.array(keys).astype(int)
        keys = list(keys.astype(str))
        data = convert2dict(f, keys, fname, valid_mods, valid_nodes)
    return data


@DATASETS.register_module()
class DataCacher(object):
    CLASSES = None
    def __init__(self,
                 hdf5_fnames=[],
                 cache_dir= f'/dev/shm/cache_train/',
                 fps=20,
                 valid_mods=['mocap', 'zed_camera_left', 'zed_camera_depth'],
                 valid_nodes=[1,2,3,4],
                 min_x=-2162.78244, max_x=4157.92774,
                 min_y=-1637.84491, max_y=2930.06133,
                 min_z=0.000000000, max_z=903.616290,
                 normalized_position=False,
                 max_len=None,
                 truck_w=30/100,
                 truck_h=15/100,
                 include_z=True,
                 **kwargs):
        self.valid_mods = valid_mods
        self.valid_nodes = ['node_%d' % n for n in valid_nodes]
        self.cache_dir = cache_dir
        self.min_x = min_x
        self.max_x = max_x
        self.len_x = 7000
        self.min_y = min_y
        self.max_y = max_y
        self.len_y = 5000
        self.min_z = min_z
        self.max_z = max_z
        self.len_z = 1000
        self.normalized_position = normalized_position
        self.truck_w = truck_w
        self.truck_h = truck_h
        self.include_z = include_z
        self.hdf5_fnames = hdf5_fnames
        self.fps = fps
        self.class2idx = {'truck': 1, 'node': 0}
        self.max_len = max_len


    def cache(self):
        # os.makedirs(self.cache_dir, exists_ok=True)
        if not os.path.exists(self.cache_dir):
            os.makedirs(self.cache_dir, exist_ok=False)
            data = {}
            for fname in self.hdf5_fnames:
                chunk = load_chunk(fname, self.valid_mods, self.valid_nodes)
                for ms, val in chunk.items():
                    if ms in data.keys():
                        for k, v in val.items():
                            if k in data[ms].keys():
                                data[ms][k].update(v)
                            else:
                                data[ms][k] = v
                    else:
                        data[ms] = val

            buffers = self.fill_buffers(data)
            self.active_keys = sorted(buffers[-1].keys())
            
            count = 0
            for i in range(len(buffers)):
                missing = False
                for key in self.active_keys:
                    if key not in buffers[i].keys():
                        missing = True
                if missing:
                    count += 1
                    continue
                else:
                    break
            buffers = buffers[count:]

            if self.max_len is not None:
                buffers = buffers[0:self.max_len]
        
        
            # final_dir = f'{self.cache_dir}/{self.name}'
            # if not os.path.exists(final_dir):
                # os.mkdir(final_dir)
            # self.fnames = []
            for i in trange(len(buffers)):
                buff = buffers[i]
                fname = '%s/tmp_%09d.pickle' % (self.cache_dir, i)
                with open(fname, 'wb') as f:
                    pickle.dump(buff, f)
                # self.fnames.append(fname)
        
        self.fnames = sorted(glob.glob(f'{self.cache_dir}/*.pickle'))
        with open(self.fnames[-1], 'rb') as f:
            buff = pickle.load(f)
            self.active_keys = sorted(buff.keys())
        self.fnames = self.fnames[0:self.max_len]
        return self.fnames, self.active_keys

    # self.fnames = glob.glob(f'{cache_dir}/{self.name}/*.pickle')
    # with open(self.fnames[-1], 'rb') as f:
        # buff = pickle.load(f)
        # self.active_keys = sorted(buff.keys())
    # self.fnames = sorted(self.fnames)[0:max_len]
    # self.nodes = set([key[1] for key in self.active_keys if 'node' in key[1]])

    # def __len__(self):
        # return len(self.fnames)
    
    def fill_buffers(self, all_data):
        buffers = []
        # buff = self.init_buffer()
        buff = {}
        factor = 100 // self.fps
        num_frames = 0
        keys = sorted(list(all_data.keys()))
        prev_num_objs = None
        for time in tqdm(keys, desc='filling buffers'):
            save_frame = False
            data = all_data[time]
            for key in data.keys():
                if key == 'mocap':
                    mocap_data = json.loads(data['mocap'])
                    if self.normalized_position:
                        gt_pos = torch.tensor([d['normalized_position'] for d in mocap_data])
                    else:
                        gt_pos = torch.tensor([d['position'] for d in mocap_data])
                        gt_pos[..., 0] += np.abs(self.min_x)
                        gt_pos[..., 1] += np.abs(self.min_y)
                        gt_pos[..., 2] += np.abs(self.min_z)
                        gt_pos /= 1000
                    gt_rot = torch.tensor([d['rotation'] for d in mocap_data])
                    
                    corners, grids = [], []
                    for k in range(len(gt_rot)):
                        angle = rot2angle(gt_rot[k], return_rads=False)
                        rec, grid = gen_rectange(gt_pos[k], angle, w=self.truck_w, h=self.truck_h)
                        corners.append(rec.get_corners())
                        if self.include_z:
                            z_val = gt_pos[k][-1]
                            z_vals = torch.ones(len(grid), 1) * z_val
                            grid = torch.cat([grid, z_vals], dim=-1)
                        grids.append(grid)

                    grids = torch.stack(grids)

                    corners = np.stack(corners)
                    corners = torch.tensor(corners).float()

                    gt_labels = torch.tensor([self.class2idx[d['type']] for d in mocap_data])
                    gt_ids = torch.tensor([d['id'] for d in mocap_data])
                    is_node = gt_labels == 0
                    
                    node_pos = gt_pos[is_node] * 100
                    node_pos = node_pos[..., 0:2]
                    node_ids = gt_ids[is_node]
                    
                    # gt_pos = gt_pos[~is_node]
                    # z_is_zero = gt_pos[:, -1] == 0.0
                    # if torch.any(z_is_zero):
                        # continue
                    final_mask = ~is_node 
                    if not self.include_z:
                        gt_pos = gt_pos[..., 0:2]
                    gt_pos = gt_pos[final_mask] * 100
                    gt_grid = grids[final_mask] * 100
                    gt_rot = gt_rot[final_mask] 
                    gt_ids = gt_ids[final_mask] - 4
                    if len(gt_pos) < 2:
                        zeros = torch.zeros(2 - len(gt_pos), gt_pos.shape[-1])
                        gt_pos = torch.cat([gt_pos, zeros - 1])
                        
                        zeros = torch.zeros(2 - len(gt_grid), 450, 2)
                        gt_grid = torch.cat([gt_grid, zeros - 1])

                        zeros = torch.zeros(2 - len(gt_rot), 9)
                        gt_rot = torch.cat([gt_rot, zeros - 1])
                        
                        zeros = torch.zeros(2 - len(gt_ids))
                        gt_ids = torch.cat([gt_ids, zeros - 1])
                        
                    buff[('mocap', 'mocap')] = {
                        'gt_positions': gt_pos,
                        #'gt_labels': gt_labels[final_mask].long(),
                        'gt_ids': gt_ids.long(),
                        'gt_rot': gt_rot,
                        #'gt_corners': corners[final_mask],
                        'gt_grids': gt_grid,
                        'node_pos': node_pos,
                        'node_ids': node_ids
                    }
                    num_frames += 1
                    save_frame = True

                if 'node' in key:
                    for k, v in data[key].items():
                        if k in self.valid_mods:
                            buff[(k, key)] = v
            
            if save_frame and num_frames % factor == 0:
                new_buff = copy.deepcopy(buff)
                buffers.append(new_buff)
        return buffers
