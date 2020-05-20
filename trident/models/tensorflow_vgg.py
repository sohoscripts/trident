from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import inspect
import math
import os
import uuid
from collections import *
from collections import deque
from copy import copy, deepcopy
from functools import partial
from itertools import repeat

import numpy as np
import tensorflow as tf
from trident.backend.common import *
from trident.backend.model import *
from trident.backend.tensorflow_backend import to_numpy, to_tensor, Layer, Sequential,load
from trident.data.image_common import *
from trident.data.utils import download_model_from_google_drive
from trident.layers.tensorflow_activations import get_activation, Identity, Relu
from trident.layers.tensorflow_blocks import *
from trident.layers.tensorflow_layers import *
from trident.layers.tensorflow_normalizations import get_normalization
from trident.layers.tensorflow_pooling import *
from trident.optims.tensorflow_trainer import ImageClassificationModel

__all__ = ['VGG19','VGG11','VGG13','VGG16']

_session = get_session()

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




cfgs = {
    'A': [64, 'M', 128, 'M', 256, 256, 'M', 512, 512, 'M', 512, 512, 'M'],
    'B': [64, 64, 'M', 128, 128, 'M', 256, 256, 'M', 512, 512, 'M', 512, 512, 'M'],
    'D': [64, 64, 'M', 128, 128, 'M', 256, 256, 256, 'M', 512, 512, 512, 'M', 512, 512, 512, 'M'],
    'E': [64, 64, 'M', 128, 128, 'M', 256, 256, 256, 256, 'M', 512, 512, 512, 512, 'M', 512, 512, 512, 512, 'M'],
}


def make_vgg_layers(cfg, num_classes=1000,input_shape=(224,224,3),include_top=True):
    layers = []
    in_channels = 3
    block=1
    conv=1
    vgg=Sequential()
    for v in cfg:
        if v == 'M':
            vgg.add_module('block{0}_pool'.format(block),MaxPool2d(kernel_size=2, strides=2,use_bias=True,name='block{0}_pool'.format(block)))
            block += 1
            conv = 1
        else:
            if len(vgg)==0:
                vgg.add_module('block{0}_conv{1}'.format(block,conv),Conv2d((3,3),v,auto_pad=True,activation=None,use_bias=True,name='block{0}_conv{1}'.format(block,conv)))
            else:
                vgg.add_module('block{0}_conv{1}'.format(block, conv), Conv2d((3, 3), v, auto_pad=True, activation=None, use_bias=True,name='block{0}_conv{1}'.format(block, conv)))

            vgg.add_module('block{0}_relu{1}'.format(block, conv),Relu(name='block{0}_relu{1}'.format(block, conv)))
            conv+=1
            in_channels = v
    if include_top==True:
        vgg.add_module('flattened', Flatten())
        vgg.add_module('fc1',Dense(4096,use_bias=True, activation='relu'))
        vgg.add_module('drop1', Dropout(0.5))
        vgg.add_module('fc2', Dense(4096, use_bias=True,activation='relu'))
        vgg.add_module('drop2', Dropout(0.5))
        vgg.add_module('fc3', Dense(num_classes,use_bias=True,activation='softmax'))


    model = ImageClassificationModel(input_shape=input_shape, output=vgg)
    model.signature = get_signature(model.model.forward)
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'imagenet_labels1.txt'), 'r',
              encoding='utf-8-sig') as f:
        labels = [l.rstrip() for l in f]
        model.class_names = labels
    model.preprocess_flow = [resize((input_shape[0], input_shape[1]), keep_aspect=True),to_bgr(),
                             normalize([103.939, 116.779, 123.68], [1, 1, 1])]


    # model.summary()

    return model


#vgg11 =make_vgg_layers(cfgs['A'], 1000)
def VGG11(include_top=True,
             pretrained=True,
             input_shape=None,
             classes=1000,
             **kwargs):
    if input_shape is not None and len(input_shape)==3:
        input_shape=tuple(input_shape)
    else:
        input_shape=(224, 224,3)
    vgg11 =make_vgg_layers(cfgs['A'], classes)
    vgg11.input_shape =input_shape
    if pretrained==True:
       print('There is no pretrained Vgg11 in tensorflow backend')
    return vgg11





#vgg13 =make_vgg_layers(cfgs['B'],  1000)
def VGG13(include_top=True,
             pretrained=True,
             input_shape=None,
             classes=1000,
             **kwargs):
    if input_shape is not None and len(input_shape)==3:
        input_shape=tuple(input_shape)
    else:
        input_shape=(224,224 ,3)
    vgg13 =make_vgg_layers(cfgs['B'], classes)

    if pretrained==True:
        print('There is no pretrained Vgg13 in tensorflow backend')
    return vgg13


#vgg16 =make_vgg_layers(cfgs['D'],  1000)
def VGG16(include_top=True,
             pretrained=True,
             input_shape=None,
             classes=1000,
             **kwargs):
    if input_shape is not None and len(input_shape)==3:
        input_shape=tuple(input_shape)
    else:
        input_shape=(224, 224,3)
    vgg16 =make_vgg_layers(cfgs['D'], classes)
    vgg16.input_shape =input_shape
    if pretrained==True:
        download_model_from_google_drive('1fozCY4Yv_ud5UGpv7q4M9tcxZ2ryDCTb',dirname,'vgg16_tf.pth')
        recovery_model=load(os.path.join(dirname,'vgg16_tf.pth'))
        recovery_model.name = 'vgg16'
        recovery_model.eval()

        if include_top==False:
            [recovery_model.__delitem__(-1) for i in range(7)]
        else:
            if classes!=1000:
                recovery_model.fc3=Dense(classes,use_bias=True,activation='softmax')

        vgg16.model=recovery_model
    return vgg16

#vgg19 =make_vgg_layers(cfgs['E'], 1000)
def VGG19(include_top=True,
             pretrained=True,
             input_shape=None,
             classes=1000,
             **kwargs):
    if input_shape is not None and len(input_shape)==3:
        input_shape=tuple(input_shape)
    else:
        input_shape=(224,224 ,3)
    vgg19 =make_vgg_layers(cfgs['E'], classes)
    vgg19.input_shape =input_shape
    if pretrained==True:
        download_model_from_google_drive('1nXKMsYklBimtqs7ZRv0dQ-RIqNvgopVh',dirname,'vgg19_tf.pth')
        recovery_model=load(os.path.join(dirname,'vgg19_tf.pth'))
        recovery_model.name = 'vgg19'
        recovery_model.eval()

        if include_top==False:
            [recovery_model.__delitem__(-1) for i in range(7)]
        else:
            if classes!=1000:
                recovery_model.fc3=Dense(classes,use_bias=True,activation='softmax')
        vgg19.model=recovery_model
    return vgg19