# Copyright (c) OpenMMLab. All rights reserved.
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import lap 
from mmdet.models import build_detector, build_head
from collections import OrderedDict

import torch.distributed as dist
from mmtrack.core import outs2results, results2outs
# from mmtrack.models.mot import BaseMultiObjectTracker
from mmcv.runner import BaseModule, auto_fp16
from ..builder import MODELS, build_tracker

from mmdet.core import bbox_xyxy_to_cxcywh, bbox_cxcywh_to_xyxy, reduce_mean
import copy

def linear_assignment(cost_matrix):
    _, x, y = lap.lapjv(cost_matrix, extend_cost=True)
    return np.array([[y[i], i] for i in x if i >= 0])

@MODELS.register_module()
class BaseMocapModel(BaseModule):
    def __init__(self,
                 detector=None,
                 *args,
                 **kwargs):
        super().__init__(*args, **kwargs)
        if detector is not None:
            self.img_detector = build_detector(detector)
            self.depth_detector = copy.deepcopy(self.img_detector)
            self.shared_head = copy.deepcopy(self.img_detector.bbox_head)

        self.ctn = nn.Sequential(
            nn.Linear(256, 256),
            nn.GELU(),
            nn.Linear(256, 256),
            nn.GELU(),
        )
        self.coord_pred_head = nn.Linear(256, 2)


    
    def forward(self, data, return_loss=True, **kwargs):
        """Calls either :func:`forward_train` or :func:`forward_test` depending
        on whether ``return_loss`` is ``True``.

        Note this setting will change the expected inputs. When
        ``return_loss=True``, img and img_meta are single-nested (i.e. Tensor
        and List[dict]), and when ``resturn_loss=False``, img and img_meta
        should be double nested (i.e.  List[Tensor], List[List[dict]]), with
        the outer list indicating test time augmentations.
        """
        if return_loss:
            return self.forward_train(data, **kwargs)
        else:
            return self.forward_test(data, **kwargs)

    def foreward_test(self, data, **kwargs):
        pass

    def forward_train(self, data, **kwargs):
        img = data['zed_camera_left']['img']
        img_metas = data['zed_camera_left']['img_metas']
        img_metas[0]['batch_input_shape'] = (img.shape[2], img.shape[3])
        
        bbox_head = self.img_detector.bbox_head
        with torch.no_grad():
            feats = self.img_detector.extract_feat(img)[0]
        query_embeds = bbox_head.query_embedding.weight 
        query_embeds_img = bbox_head.forward_transformer(
            feats, query_embeds, img_metas
        ).mean(dim=0)

        img = data['zed_camera_depth']['img']
        img_metas = data['zed_camera_depth']['img_metas']
        img_metas[0]['batch_input_shape'] = (img.shape[2], img.shape[3])
        img = img.expand(-1, 3, -1, -1)
        
        bbox_head = self.depth_detector.bbox_head
        with torch.no_grad():
            feats = self.depth_detector.extract_feat(img)[0]
        query_embeds = bbox_head.query_embedding.weight 
        query_embeds_depth = bbox_head.forward_transformer(
            feats, query_embeds, img_metas
        ).mean(dim=0)

        final_embeds = torch.cat([query_embeds_img, query_embeds_depth], dim=1)
        

        preds = self.coord_pred_head(final_embeds)
        # preds = preds.squeeze()
        preds = preds.mean(dim=0).mean(dim=0)
    
        gt_pos = data['mocap']['gt_positions'].squeeze()[-2][0:2]

        loss = (gt_pos - preds)**2
        return {'loss': loss.mean()}
        # gt_ids = data['mocap']['gt_ids'][-2]
        # gt_labels = data['mocap']['gt_labels'][-2]

        import ipdb; ipdb.set_trace() # noqa

        query_embeds = query_embeds.mean(dim=0).squeeze()
        coord_embeds = self.ctn(query_embeds)
        coord_preds = self.coord_pred_head(coord_embeds).sigmoid()
        dists = self.dist_fn(coord_preds.unsqueeze(0), gt_coords.unsqueeze(1))
        dists = dists.squeeze().t()
        matches = linear_assignment(dists.detach().cpu().numpy()) 
        
        mse_loss_val = 0
        for (pred_idx, gt_idx) in matches:
            mse_loss_val += dists[pred_idx, gt_idx]
        mse_loss_val = mse_loss_val / len(matches)

        track_embeds = query_embeds[matches[:, 0]]
        losses = {'loss_mse_frame1': mse_loss_val}
        
        with torch.no_grad():
            feats = self.detector.extract_feat(ref_img)[0]
        track_embeds = bbox_head.forward_transformer(
            feats, track_embeds, img_metas
        )
        track_embeds = track_embeds.mean(dim=0).squeeze()
        coord_embeds = self.ctn(track_embeds)
        coord_preds = self.coord_pred_head(coord_embeds).sigmoid()

        loss_val = 0
        for idx, cp in enumerate(coord_preds):
            dist = self.dist_fn(cp, ref_gt_coords[idx])
            loss_val += dist
        loss_val /= len(coord_preds)
        # import ipdb; ipdb.set_trace() # noqa
        # dists = self.dist_fn(coord_preds.unsqueeze(0), ref_gt_coords.unsqueeze(1))
        # dists = torch.diag(dists.squeeze())

        losses['loss_mse_frame2'] = loss_val
        return losses


    def simple_test(self, img, img_metas, rescale=False):
        pass

    def train_step(self, data, optimizer):
        """The iteration step during training.

        This method defines an iteration step during training, except for the
        back propagation and optimizer updating, which are done in an optimizer
        hook. Note that in some complicated cases or models, the whole process
        including back propagation and optimizer updating is also defined in
        this method, such as GAN.

        Args:
            data (dict): The output of dataloader.
            optimizer (:obj:`torch.optim.Optimizer` | dict): The optimizer of
                runner is passed to ``train_step()``. This argument is unused
                and reserved.

        Returns:
            dict: It should contain at least 3 keys: ``loss``, ``log_vars``,
            ``num_samples``.

            - ``loss`` is a tensor for back propagation, which can be a
            weighted sum of multiple losses.
            - ``log_vars`` contains all the variables to be sent to the
            logger.
            - ``num_samples`` indicates the batch size (when the model is
            DDP, it means the batch size on each GPU), which is used for
            averaging the logs.
        """
        losses = self(data)
        loss, log_vars = self._parse_losses(losses)
        
        num_samples = len(data['mocap']['gt_positions'])

        outputs = dict(
            loss=loss, log_vars=log_vars, num_samples=num_samples)

        return outputs

    def _parse_losses(self, losses):
        """Parse the raw outputs (losses) of the network.

        Args:
            losses (dict): Raw output of the network, which usually contain
                losses and other necessary information.

        Returns:
            tuple[Tensor, dict]: (loss, log_vars), loss is the loss tensor
            which may be a weighted sum of all losses, log_vars contains
            all the variables to be sent to the logger.
        """
        log_vars = OrderedDict()
        for loss_name, loss_value in losses.items():
            if isinstance(loss_value, torch.Tensor):
                log_vars[loss_name] = loss_value.mean()
            elif isinstance(loss_value, list):
                log_vars[loss_name] = sum(_loss.mean() for _loss in loss_value)
            else:
                raise TypeError(
                    f'{loss_name} is not a tensor or list of tensors')

        loss = sum(_value for _key, _value in log_vars.items()
                   if 'loss' in _key)

        log_vars['loss'] = loss
        for loss_name, loss_value in log_vars.items():
            # reduce loss when distributed training
            if dist.is_available() and dist.is_initialized():
                loss_value = loss_value.data.clone()
                dist.all_reduce(loss_value.div_(dist.get_world_size()))
            log_vars[loss_name] = loss_value.item()

        return loss, log_vars



    def val_step(self, data, optimizer):
        """The iteration step during validation.

        This method shares the same signature as :func:`train_step`, but used
        during val epochs. Note that the evaluation after training epochs is
        not implemented with this method, but an evaluation hook.
        """
        losses = self(**data)
        loss, log_vars = self._parse_losses(losses)

        outputs = dict(
            loss=loss, log_vars=log_vars, num_samples=len(data['img_metas']))

        return outputs

