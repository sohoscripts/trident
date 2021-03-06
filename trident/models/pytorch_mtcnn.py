from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import inspect
import itertools
import math
import os
import uuid
from collections import *
from collections import deque
from copy import copy, deepcopy
from functools import partial
from itertools import repeat

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from matplotlib.collections import PolyCollection
from torch._six import container_abcs
from torch.nn import init
from torch.nn.parameter import Parameter

from trident.backend.common import *
from trident.backend.tensorspec import *
from trident.backend.pytorch_backend import *
from trident.backend.pytorch_backend import to_numpy, to_tensor, Layer, Sequential, Combine, load
from trident.backend.pytorch_ops import *
from trident.data.bbox_common import clip_boxes_to_image, nms
from trident.data.image_common import *
from trident.data.utils import download_model_from_google_drive
from trident.layers.pytorch_activations import get_activation, Identity, PRelu
from trident.layers.pytorch_blocks import *
from trident.layers.pytorch_layers import *
from trident.layers.pytorch_normalizations import get_normalization
from trident.layers.pytorch_pooling import *
from trident.optims.pytorch_trainer import *
from trident.optims.pytorch_trainer import ImageDetectionModel

__all__ = ['Pnet','Rnet','Onet','Mtcnn']

_session = get_session()
_device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
_epsilon=_session.epsilon
_trident_dir=_session.trident_dir


dirname = os.path.join(_trident_dir, 'models')
if not os.path.exists(dirname):
    try:
        os.makedirs(dirname)
    except OSError:
        # Except permission denied and potential race conditions
        # in multi-threaded environments.
        pass


def p_net():
    return Sequential(
    Conv2d((3,3),10,strides=1,auto_pad=False,use_bias=True,name='conv1'),
    PRelu(num_parameters=1),
    MaxPool2d((2,2),strides=2,auto_pad=False),
    Conv2d((3, 3), 16, strides=1, auto_pad=False,use_bias=True,name='conv2'),
    PRelu(num_parameters=1),
    Conv2d((3,3),32,strides=1,auto_pad=False,use_bias=True,name='conv3'),
    PRelu(num_parameters=1),
    Combine(
        Conv2d((1,1),1,strides=1,auto_pad=False,use_bias=True,activation='sigmoid',name='conv4_1'),
        Conv2d((1,1),4,strides=1,auto_pad=False,use_bias=True,name='conv4_2'),
        Conv2d((1,1),10,strides=1,auto_pad=False,use_bias=True,name='conv4_3')),name='pnet')



def r_net():
    return Sequential(
    Conv2d((3,3),28,strides=1,auto_pad=False,use_bias=True,name='conv1'),
    PRelu(num_parameters=1),
    MaxPool2d((3,3),strides=2,auto_pad=False),
    Conv2d((3, 3), 48, strides=1, auto_pad=False,use_bias=True,name='conv2'),
    PRelu(num_parameters=1),
    MaxPool2d((3,3),strides=2,auto_pad=False),
    Conv2d((2,2),64,strides=1,auto_pad=False,use_bias=True,name='conv3'),
    PRelu(num_parameters=1),
    Flatten(),
    Dense(128,activation=None,use_bias=True,name='conv4'),
    PRelu(num_parameters=1),
    Combine(
        Dense(1,activation='sigmoid',use_bias=True,name='conv5_1'),
        Dense(4,activation=None,use_bias=True,name='conv5_2'),
        Dense(10,activation=None,use_bias=True,name='conv5_3'))
    ,name='rnet')



def o_net():
    return Sequential(
    Conv2d((3,3),32,strides=1,auto_pad=False,use_bias=True,name='conv1'),
    PRelu(num_parameters=1),
    MaxPool2d((3,3),strides=2,auto_pad=False),
    Conv2d((3, 3), 64, strides=1, auto_pad=False,use_bias=True,name='conv2'),
    PRelu(num_parameters=1),
    MaxPool2d((3,3),strides=2,auto_pad=False),
    Conv2d((3,3),64,strides=1,auto_pad=False,use_bias=True,name='conv3'),
    PRelu(num_parameters=1),
    MaxPool2d((2, 2), strides=2,auto_pad=False),
    Conv2d((2, 2), 128, strides=1, auto_pad=False,use_bias=True,name='conv4'),
    PRelu(num_parameters=1),
    Flatten(),
    Dense(256,activation=None,use_bias=True,name='conv5'),
    PRelu(num_parameters=1),
    Combine(
        Dense(1,activation='sigmoid',use_bias=True,name='conv6_1'),
        Dense(4,activation=None,use_bias=True,name='conv6_2'),
        Dense(10,activation=None,use_bias=True,name='conv6_3')),name='onet')



def Pnet(pretrained=True,
             input_shape=(3,12,12),
             **kwargs):
    if input_shape is not None and len(input_shape)==3:
        input_shape=tuple(input_shape)
    else:
        input_shape=(3,12,12)
    pnet =ImageDetectionModel(input_shape=(3,12,12),output=p_net())
    pnet.preprocess_flow = [normalize(0, 255), image_backend_adaption]
    if pretrained==True:
        download_model_from_google_drive('1w9ahipO8D9U1dAXMc2BewuL0UqIBYWSX',dirname,'pnet.pth')
        recovery_model=load(os.path.join(dirname,'pnet.pth'))
        recovery_model = fix_layer(recovery_model)
        recovery_model.to(_device)
        pnet.model=recovery_model
    return pnet


def Rnet(pretrained=True,
             input_shape=(3,24,24),
             **kwargs):
    if input_shape is not None and len(input_shape)==3:
        input_shape=tuple(input_shape)
    else:
        input_shape=(3,24,24)
    rnet =ImageDetectionModel(input_shape=(3,24,24),output=r_net())
    rnet.preprocess_flow = [normalize(0, 255), image_backend_adaption]
    if pretrained==True:
        download_model_from_google_drive('1CH7z133_KrcWMx9zXAblMCV8luiQ3wph',dirname,'rnet.pth')
        recovery_model=load(os.path.join(dirname,'rnet.pth'))
        recovery_model = fix_layer(recovery_model)
        recovery_model.to(_device)
        rnet.model=recovery_model
    return rnet

def Onet(pretrained=True,
             input_shape=(3,48,48),
             **kwargs):
    if input_shape is not None and len(input_shape)==3:
        input_shape=tuple(input_shape)
    else:
        input_shape=(3,48,48)
    onet =ImageDetectionModel(input_shape=(3,48,48),output=o_net())
    onet.preprocess_flow = [normalize(0, 255), image_backend_adaption]
    if pretrained==True:
        download_model_from_google_drive('1a1dAlSzJOAfIz77Ic38JMQJYWDG_b7-_',dirname,'onet.pth')
        recovery_model=load(os.path.join(dirname,'onet.pth'))
        recovery_model = fix_layer(recovery_model)
        recovery_model.to(_device)
        onet.model=recovery_model
    return onet



class DetectorHead(Layer):
    def __init__(self, cellsize=12,threshould=0.5, min_size=10,**kwargs):
        super(DetectorHead, self).__init__(**kwargs)
        self.cellsize=cellsize
        self.threshould=threshould
        self.min_size=min_size

        self._built =True

    def forward(self, input,**kwargs):
        boxprobs,boxregs,landscape=input
        boxprobs=boxprobs[0]
        height,width=boxprobs.shape[1:]
        if boxprobs.size(0)==2:
            boxprobs=boxprobs[1:,:,:]
        strides=2
        boxregs=boxregs[0]
        input_shape=boxprobs.size()
        grid=meshgrid(boxprobs.size(1),boxprobs.size(2))
        grid=grid.view(2,-1)
        score = boxprobs[0]
        y,x = torch.where(score>= self.threshould)
        boxregs = boxregs.permute(1,2,0)

        score = score[(y,x )]
        reg=boxregs[(y,x )].transpose(1,0)
        bb = torch.stack([x,y], dim=0)

        q1 = (strides * bb + 1)
        q2 =(strides * bb +self.cellsize - 1 + 1)

        w = q2[0, :] - q1[0, :] + 1
        h = q2[1, :] - q1[1, :] + 1


        b1 = q1[0, :] + reg[0, :] * w
        b2 = q1[1, :] + reg[1, :] * h
        b3 =q2[0, :] + reg[2, :] * w
        b4 =q2[1, :] + reg[3, :] * h

        boxs=torch.stack([b1,b2,b3,b4,score],dim=-1)
        #keep =torchvision.ops.boxes.remove_small_boxes(boxs[:,:4],min_size=self.min_size)
        #boxs=boxs[keep]
        #print('total {0} boxes cutoff={1} '.format(len(x), cutoff))
        if boxs is None or len(boxs.size()) == 0:
            return None
        elif len(boxs.size())==1:
            boxs=boxs.unsqueeze(0)
        return boxs

def remove_useless_boxes(boxes,image_size=None,min_size=5):
    height, width = image_size if image_size is not None else (None,None)

    x1, y1, x2, y2, score = [boxes[:, i] for i in range(5)]
    area = (x2 - x1 + 1) * (y2 - y1 + 1)
    boxes=boxes[area>min_size*min_size]
    x1, y1, x2, y2, score = [boxes[:, i] for i in range(5)]
    greater0=x1.gt(0).float() * x2.gt(0).float() * y1.gt(0).float() * y1.gt(0).float()
    boxes=boxes[greater0>0]
    x1, y1, x2, y2, score = [boxes[:, i] for i in range(5)]
    w=(x2 - x1 )
    boxes=boxes[w>1]
    x1, y1, x2, y2, score = [boxes[:, i] for i in range(5)]
    h=(y2 - y1)
    boxes = boxes[h > 1]


    return boxes



def calibrate_box(bboxes, offsets):
    """
        Transform bounding boxes to be more like true bounding boxes.
        'offsets' is one of the outputs of the nets.
    """
    x1, y1, x2, y2 = [bboxes[:, i] for i in range(4)]
    w = x2 - x1 + 1.0
    h = y2 - y1 + 1.0
    # w [w_len, 1]
    w = torch.unsqueeze(w, 1)
    # h [h_len, 1]
    h = torch.unsqueeze(h, 1)

    translation = torch.cat([w, h, w, h],-1) * offsets
    bboxes[:, 0:4] = bboxes[:, 0:4] + translation
    return bboxes



class Mtcnn(ImageDetectionModel):
    def __init__(self, pretrained=True, min_size=10,verbose=True, **kwargs):
        self.verbose = verbose
        pnet =p_net()
        self.rnet = ImageDetectionModel(input_shape=(3, 24, 24), output=r_net())._model
        self.onet = ImageDetectionModel(input_shape=(3, 48, 48), output=o_net())._model
        if pretrained == True:
            pnet =Pnet()._model
            self.rnet = Rnet()._model
            self.onet = Onet()._model
        self.min_size = min_size



        super(Mtcnn, self).__init__(input_shape=(3,224,224),output=pnet)
        self.pnet=pnet

        self.signature = get_signature(self._model.forward)
        #data preprocess
        self.preprocess_flow =[normalize(0,255)]
        self.nms_threshould = [0.9, 0.9, 0.3]
        self.detection_threshould = [0.5, 0.6, 0.9]

        pnet.add_module('pnet_detector', DetectorHead(cellsize=12, threshould=0.5, min_size=self.min_size))


    def get_image_pyrimid(self,img,min_size=None,factor= 0.709):
        if min_size is None:
            min_size=self.min_size
        min_face_area = (min_size, min_size)
        h = img.shape[0]
        w = img.shape[1]
        minl = np.amin([h, w])
        m = 12.0 / min_size
        minl = minl * m
        # create scale pyramid
        scales = []
        images = []
        factor_count = 0
        while minl >= 12:
            scales += [m * np.power(factor, factor_count)]
            scaled_img = rescale(scales[-1])(img.copy())
            if img is not None:
                for func in self.preprocess_flow:
                    if inspect.isfunction(func):
                        scaled_img=func(scaled_img)
            images.append(image_backend_adaption(scaled_img))
            minl = minl * factor
            factor_count += 1
        return images, scales

    #adjust bbox like square
    def rerec(self, bboxA, img_shape):
        """Convert bboxA to square."""

        h = bboxA[:, 3] - bboxA[:, 1]
        w = bboxA[:, 2] - bboxA[:, 0]
        max_len = maximum(w, h)

        bboxA[:, 0] = round(bboxA[:, 0] - 0.5 * (max_len - w), 0)
        bboxA[:, 1] = round(bboxA[:, 1] - 0.5 * (max_len - h), 0)
        bboxA[:, 2] = bboxA[:, 0] + max_len
        bboxA[:, 3] = bboxA[:, 1] + max_len
        return bboxA

    # 計算面積
    def area_of(self,left_top, right_bottom):
        """Compute the areas of rectangles given two corners.

        Args:
            left_top (N, 2): left top corner.
            right_bottom (N, 2): right bottom corner.

        Returns:
            area (N): return the area.
        """
        hw = right_bottom - left_top
        return clip(hw[..., 0], min=0) * clip(hw[..., 1], min=0)

    # 計算IOU(交集/聯集)
    def iou_of(self,boxes0, boxes1, eps=1e-5):
        """Return intersection-over-union (Jaccard index) of boxes.

        Args:
            boxes0 (N, 4): ground truth boxes.
            boxes1 (N or 1, 4): predicted boxes.
            eps: a small number to avoid 0 as denominator.
        Returns:
            iou (N): IoU values.
        """
        overlap_left_top = maximum(boxes0[..., :2], boxes1[..., :2])
        overlap_right_bottom = minimum(boxes0[..., 2:], boxes1[..., 2:])

        overlap_area = self.area_of(overlap_left_top, overlap_right_bottom)
        area0 = self.area_of(boxes0[..., :2], boxes0[..., 2:])
        area1 = self.area_of(boxes1[..., :2], boxes1[..., 2:])
        return overlap_area / (area0 + area1 - overlap_area + eps)

    # 基於tensor計算nms
    def boxes_nms(self,box_scores, overlap_threshold=0.5, top_k=-1):
        """Non-maximum suppression.
        Arguments:
            box_scores: a float numpy array of shape [n, 5],
                where each row is (xmin, ymin, xmax, ymax, score).
            overlap_threshold: a float number.
        Returns:
            list with indices of the selected boxes
        """
        # 如果沒有有效的候選區域則回傳空的清單
        box_scores = to_tensor(box_scores)
        if len(box_scores) == 0:
            return []
        score = box_scores[:, 4]
        boxes = box_scores[:, :4]
        # 存放過關的索引值
        picked = []
        # 依照機率信心水準升冪排序
        indexes = argsort(score, descending=False)

        while len(indexes) > 0:
            # 如此一來，最後一筆即是信心水準最高值
            # 加入至過關清單中
            current = indexes[-1]
            picked.append(current.item())

            # 計算其餘所有候選框與此當前框之間的IOU

            if 0 < top_k == len(picked) or len(indexes) == 1:
                break
            current_box = boxes[current, :]
            current_score = score[current]
            # 除了最後一筆以外的都是其餘框
            indexes = indexes[:-1]
            rest_boxes = boxes[indexes, :]
            iou = self.iou_of(
                rest_boxes,
                expand_dims(current_box, axis=0),
            )
            # IOU未超過門檻值的表示未與當前框重疊，則留下，其他排除
            indexes = indexes[iou <= overlap_threshold]
        return box_scores[picked]


    def infer_single_image(self,img,**kwargs):
        if self.model.built:
            self.model.to(self.device)
            self.model.eval()
            img=image2array(img)
            if img.shape[-1]==4:
                img=img[:,:,:3]

            imgs,scales=self.get_image_pyrimid(img)
            boxes_list=[]
            for i in range(len(scales)):
                scaled_img=imgs[i]
                inp =to_tensor(expand_dims(scaled_img, 0)).to(torch.device("cuda" if self.pnet.weights[0].data.is_cuda else "cpu")).to(self.pnet.weights[0].data.dtype)

                boxes=self.pnet(inp)
                if boxes is not None and len(boxes)>0:
                    scale=scales[i]
                    box=boxes[:,:4]/scale
                    score=boxes[:,4:]
                    boxes = torch.cat([box.round_(), score], dim=1)
                    if len(boxes) > 0:
                        boxes_list.append(boxes)

            #######################################
            #########pnet finish
            #######################################
            if len(boxes_list) > 0:
                boxes=to_tensor(torch.cat(boxes_list, dim=0))

                #print('total {0} boxes in pnet in all scale '.format(len(boxes)))
                boxes=clip_boxes_to_image(boxes,(img.shape[0],img.shape[1]))
                boxes =self.boxes_nms(boxes, overlap_threshold=self.detection_threshould[0])
                if self.verbose:
                    print('pnet:{0} boxes '.format(len(boxes)))
                #print('total {0} boxes after nms '.format(len(boxes)))
                #score = to_numpy(boxes[:, 4]).reshape(-1)
                if boxes is not None:
                    #prepare rnet input

                    boxes= self.rerec(boxes, img.shape)
                    new_arr = np.zeros((boxes.shape[0], 3, 24, 24))

                    for k in range(boxes.shape[0]):
                        box = boxes[k]
                        crop_img = img.copy()[int(box[1]):int(box[3]), int(box[0]):int(box[2]), :]
                        if crop_img.shape[0] > 0 and crop_img.shape[1] > 0:
                            new_arr[k] = resize((24, 24))(crop_img).transpose([2, 0, 1]) / 255.0
                        # else:
                        #     print(box)
                    new_arr = to_tensor(new_arr)
                    r_output1_list = []
                    r_output2_list = []
                    r_output3_list = []
                    if len(new_arr) > 16:
                        for i in range(len(new_arr) // 16 + 1):
                            if i * 16 < len(new_arr):
                                r_out1, r_out2, r_out3 = self.rnet(new_arr[i * 16:(i + 1) * 16, :, :, :])
                                r_output1_list.append(r_out1)
                                r_output2_list.append(r_out2)
                                r_output3_list.append(r_out3)
                        r_out1 = torch.cat(r_output1_list, dim=0)
                        r_out2 = torch.cat(r_output2_list, dim=0)
                        r_out3 = torch.cat(r_output3_list, dim=0)
                    else:
                        r_out1, r_out2, r_out3 = self.rnet(new_arr)

                    probs =r_out1
                    keep =probs[:, 0] > self.detection_threshould[1]
                    r_out1=r_out1[keep]

                    boxes = boxes[keep]
                    if len(boxes)==0:
                        return boxes
                    boxes[:, 4] = r_out1[:, 0]
                    r_out2 = r_out2[keep]
                    boxes=calibrate_box(boxes,r_out2)


                    #######################################
                    #########rnet finish
                    #######################################
                    boxes=self.boxes_nms(boxes, overlap_threshold=self.detection_threshould[1])
                    if self.verbose:
                        print('rnet:{0} boxes '.format(len(boxes)))
                    #print('total {0} boxes after nms '.format(len(boxes)))
                    boxes = clip_boxes_to_image(boxes, (img.shape[0], img.shape[1]))
                    boxes=self.rerec(to_tensor(boxes),img.shape)
                    new_arr=np.zeros((boxes.shape[0],3,48,48))


                    for k in range(boxes.shape[0]):
                        box=boxes[k]
                        crop_img=img.copy()[int(box[1]):int(box[3]),int(box[0]):int(box[2]),:]
                        if crop_img.shape[0]>0 and crop_img.shape[1]>0:
                            new_arr[k]=resize((48,48))(crop_img).transpose([2,0,1])/255.0
                        # else:
                        #     print(box)

                    new_arr=to_tensor(new_arr)
                    o_out1, o_out2,o_out3  = self.onet(new_arr)
                    probs = o_out1
                    keep = probs[:, 0] > self.detection_threshould[2]
                    o_out1 = o_out1[keep]
                    boxes = boxes[keep]
                    if len(boxes)==0:
                        return boxes
                    boxes[:, 4] = o_out1[:, 0]
                    o_out2 = o_out2[keep]
                    o_out3=o_out3[keep]
                    boxes = calibrate_box(boxes, o_out2)

                    landmarks_x = boxes[:, 0:1] + o_out3[:, 0::2] * (boxes[:, 2:3] - boxes[:, 0:1]+1)
                    landmarks_y = boxes[:, 1:2] + o_out3[:, 1::2] * (boxes[:, 3:4] - boxes[:, 1:2]+1)

                    boxes=torch.cat([boxes,landmarks_x,landmarks_y],dim=-1)


                    #######################################
                    #########onet finish
                    #######################################
                    boxes = self.boxes_nms(boxes, overlap_threshold=self.detection_threshould[2])
                    if self.verbose:
                        print('onet:{0} boxes '.format(len(boxes)))
                    return to_numpy(boxes)
            else:
                return None
            #idx=int(np.argmax(result,-1)[0])

        else:
            raise  ValueError('the model is not built yet.')
    def generate_bboxes(self,*outputs,threshould=0.5,scale=1):
        raise NotImplementedError
    def nms(self,bboxes):
        raise NotImplementedError


