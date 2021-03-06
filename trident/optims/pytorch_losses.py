from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import builtins
import math
from math import *

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.nn import init
from torch.nn.modules.loss import _Loss

from trident.backend.common import *
from trident.backend.pytorch_backend import *
from trident.backend.pytorch_ops import *
from trident.layers.pytorch_activations import sigmoid
from trident.optims.losses import Loss
_session = get_session()
_device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
__all__ = ['_ClassificationLoss', 'MSELoss', 'CrossEntropyLoss', 'NLLLoss', 'BCELoss', 'F1ScoreLoss', 'L1Loss', 'SmoothL1Loss', 'L2Loss', 'CosineSimilarityLoss',
           'ExponentialLoss', 'ItakuraSaitoLoss', 'MS_SSIMLoss', 'DiceLoss', 'WingLoss', 'AdaptiveWingLoss',
           'IoULoss', 'FocalLoss', 'SoftIoULoss', 'CenterLoss', 'TripletLoss', 'TripletMarginLoss',
           'LovaszSoftmax', 'PerceptionLoss', 'EdgeLoss', 'TransformInvariantLoss', 'get_loss']


class _ClassificationLoss(Loss):
    """Calculate loss for  complex classification task."""

    def __init__(self, axis=1, sample_weight=None, from_logits=False, ignore_index=-100, cutoff=None, label_smooth=False, reduction='mean', name=None, **kwargs):
        """

        Args:
            axis (int): the position where the classes is.
            sample_weight (Tensor): means the weights of  classes , it shoud be a 1D tensor and length the same as
            number of classes.
            from_logits (bool): whether the output tensor is normalized as a probability (total equal to 1)
            ignore_index (int or list of int):
            cutoff (None or decimal): the cutoff point of probability for classification, should be None of a number
            less than 1..
            is_target_onehot (bool): Is the target tensor in onehot format?
            label_smooth (bool): Should use label smoothing?
            reduction (string): the method to aggrgate loss. None means no need to aggregate, 'mean' means average loss,
                'sum' means the summation of losses,'batch_mean' means average loss cross the batch axis then
                summation them.

        Attributes:
            need_target_onehot (bool): If True, means the before loss calculation , need to transform target as one-hot format, ex. label-smooth, default is False.
            is_multiselection (bool): If True, means the classification model is multi-selection, so cannot use  any softmax process, use sigmoid and binary_crosss_entropy
            insteaded.
            is_target_onehot (bool):  If True, means we have confirmed (not just declare) the target is transformed as  one-hot format
            reduction(str): The aggregation function for loss, available options are 'sum', 'mean 'and 'batch_mean', default is 'mean'
            axis (None or int): The axis we according with for loss calculation. Default is 1.
            from_logits (bool):If True, means  the sum of all probability will equal 1.
            is_logsoftmax (bool):If True, means model  use SoftMax as last layer or use any equivalent calculation.
            sample_weight(1D tensor):The loss weight for all classes.
            ignore_index(int , list, tuple): The classes we want to ignore in the loss calculation.
            cutoff(float): Means the decision boundary in this classification model, default=0.5.
            num_classes(int):number of  all the classes.
            label_smooth (bool):If True, mean we will apply label-smoothing in loss calculation.

        """
        super(_ClassificationLoss, self).__init__(reduction=reduction, sample_weight=sample_weight,axis=axis,name=name)
        self.need_target_onehot = False
        self.is_multiselection = False
        self.is_target_onehot = False
        self.from_logits = from_logits
        self.is_logsoftmax = False
        self.ignore_index = ignore_index
        self.ignore_index_weight=None
        if cutoff is not None and not 0 < cutoff < 1:
            raise ValueError('cutoff should between 0 and 1')
        self.cutoff = cutoff
        self.num_classes = None
        self.label_smooth = label_smooth
        self.reduction=reduction
        # initilize weight

    def flatten_check(self, output, target):
        "Check that `out` and `targ` have the same number of elements and flatten them."
        if ndim(output) > 2:
            output = output.permute(1, -1).contiguous()
            output = output.view(-1, output.size(-1))
        if ndim(target) > 2:
            if target.dtype != str2dtype('long'):
                target = target.permute(1, -1).contiguous()
            target = target.view(-1, target.size(-1))

        if len(output) == len(target):
            return output, target
        else:
            raise ValueError('output and target have diffent elements.')

    def preprocess(self, output: Tensor, target: Tensor, **kwargs):
        """

        Args:
            output ():
            target ():
            **kwargs ():

        Returns:

        """
        # check num_clases
        if self.num_classes is None:
            self.num_classes = int_shape(output)[self.axis]
        #output,target=self.flatten_check(output,target)

        if self.sample_weight is None:
            self.sample_weight = ones(self.num_classes, requires_grad=False).to(get_device())
        elif len(self.sample_weight) != self.num_classes:
            raise ValueError('weight should be 1-D tensor and length equal to numbers of filters')
        elif self.sample_weight.requires_grad!=False or self.sample_weight.dtype!=output.dtype or self.sample_weight.device!=output.device:
            self.sample_weight = to_tensor(self.sample_weight, requires_grad=False).to(get_device())
        else:
            pass

        output_exp = exp(output)

        if (ndim(output) >= 1 and 'float' in str(output.dtype) and output.min() >= 0 and output.max() <= 1 ):
            self.is_logsoftmax = False
            self.from_logits = True
            output = clip(output, min=1e-8, max=1 - 1e-8)

        elif (ndim(output) >=1 and 'float' in str(output.dtype) and output_exp.min() >= 0 and output_exp.max() <= 1 ):
            self.is_logsoftmax = True
            self.from_logits = True
            output = clip(output, max=- 1e-8)
        else:
            self.is_logsoftmax = False
            self.from_logits = False

        self.ignore_index_weight=ones_like(self.sample_weight,requires_grad=False,dtype=output.dtype).to(get_device())
        # ignore_index
        with torch.no_grad():
            if isinstance(self.ignore_index, int) and 0 <= self.ignore_index < self.num_classes:
                self.ignore_index_weight[self.ignore_index] = 0
            elif isinstance(self.ignore_index, (list, tuple)):
                for idx in self.ignore_index:
                    if isinstance(idx, int) and 0 <= idx < int_shape(output)[self.axis]:
                        self.ignore_index_weight[idx] = 0
        if self.label_smooth:
            self.need_target_onehot = True
        if target.dtype == str2dtype('long'):
            self.is_target_onehot = False
        elif target.dtype != str2dtype('long') and (target.min() >= 0 and target.max() <= 1 and abs(output_exp.sum(-1).mean() - 1) < 1e-4):
            target = clip(target, min=1e-8, max=1 - 1e-8)
            self.is_target_onehot = True

        # need target onehot but currently not
        if  target.dtype==torch.long and self.need_target_onehot == True and self.is_target_onehot == False:
            target = make_onehot(target, num_classes=self.num_classes, axis=self.axis).to(get_device())
            target.require_grads=False
            self.is_target_onehot=True
            if self.label_smooth:
                target = target * (torch.Tensor(target.size()).uniform_(0.9, 1).to(output.device))
                self.is_target_onehot = True
            target.require_grads=False

        # setting cutoff
        # if self.cutoff is not None:
        #     mask = (output > self.cutoff).to(output.dtype)
        #     output = output * mask
        return output, target

    def calculate_loss(self, output, target, **kwargs):
        """ Calculate the unaggregate loss.
        The loss function calculation logic should define here., please dont't aggregate the loss in this phase.

        Args:
            output (tf.Tensor):
            target (tf.Tensor):
        """
        ##dont do aggregation
        raise NotImplementedError


    def forward(self, output: Tensor, target: Tensor, **kwargs) -> 'loss':
        """

            Args:
                output (tf.Tensor):
                target (tf.Tensor):

            Returns:
                calculated loss

            """
        try:

            loss = self.calculate_loss(*self.preprocess(output, target,**kwargs))
            loss=self._handel_abnormal(loss)
            loss = self._get_reduction(loss)
            return loss
        except Exception as e:
            print(e)
            PrintException()
            raise e

class _PairwiseLoss(Loss):
    """Calculate loss for  complex classification task."""

    def __init__(self, axis=None,  reduction='mean', name=None, **kwargs):
        """

        Args:
            axis (int): the position where the classes is.
            sample_weight (Tensor): means the weights of  classes , it shoud be a 1D tensor and length the same as
            number of classes.
            from_logits (bool): whether the output tensor is normalized as a probability (total equal to 1)
            ignore_index (int or list of int):
            cutoff (None or decimal): the cutoff point of probability for classification, should be None of a number
            less than 1..
            is_target_onehot (bool): Is the target tensor in onehot format?
            label_smooth (bool): Should use label smoothing?
            reduction (string): the method to aggrgate loss. None means no need to aggregate, 'mean' means average loss,
                'sum' means the summation of losses,'batch_mean' means average loss cross the batch axis then
                summation them.

        Attributes:
            need_target_onehot (bool): If True, means the before loss calculation , need to transform target as one-hot format, ex. label-smooth, default is False.
            is_multiselection (bool): If True, means the classification model is multi-selection, so cannot use  any softmax process, use sigmoid and binary_crosss_entropy
            insteaded.
            is_target_onehot (bool):  If True, means we have confirmed (not just declare) the target is transformed as  one-hot format
            reduction(str): The aggregation function for loss, available options are 'sum', 'mean 'and 'batch_mean', default is 'mean'
            axis (None or int): The axis we according with for loss calculation. Default is 1.
            from_logits (bool):If True, means  the sum of all probability will equal 1.
            is_logsoftmax (bool):If True, means model  use SoftMax as last layer or use any equivalent calculation.
            sample_weight(1D tensor):The loss weight for all classes.
            ignore_index(int , list, tuple): The classes we want to ignore in the loss calculation.
            cutoff(float): Means the decision boundary in this classification model, default=0.5.
            num_classes(int):number of  all the classes.
            label_smooth (bool):If True, mean we will apply label-smoothing in loss calculation.

        """
        super(_PairwiseLoss, self).__init__(reduction=reduction,axis=axis,name=name)



        # initilize weight

    def flatten_check(self, output, target):
        "Check that `out` and `targ` have the same number of elements and flatten them."
        if ndim(output) > 2:
            output = output.permute(1, -1).contiguous()
            output = output.view(-1, output.size(-1))
        if ndim(target) > 2:
            if target.dtype != str2dtype('long'):
                target = target.permute(1, -1).contiguous()
            target = target.view(-1, target.size(-1))

        if len(output) == len(target):
            return output, target
        else:
            raise ValueError('output and target have diffent elements.')

    def preprocess(self, output: Tensor, target: Tensor, **kwargs):
        """

        Args:
            output ():
            target ():
            **kwargs ():

        Returns:

        """
        # check num_clases
        #output, target = self.flatten_check(output, target)
        if output.shape == target.shape:
            return output, target
        elif target.dtype==torch.int64 and ndim(output)==ndim(target)+1:
            num_class=int_shape(output)[self.axis]
            target=make_onehot(target,num_class,self.axis).float()
        return output, target


    def calculate_loss(self, output, target, **kwargs):
        """ Calculate the unaggregate loss.
        The loss function calculation logic should define here., please dont't aggregate the loss in this phase.

        Args:
            output (tf.Tensor):
            target (tf.Tensor):
        """
        ##dont do aggregation
        raise NotImplementedError


    def forward(self, output: Tensor, target: Tensor, **kwargs) -> 'loss':
        """

            Args:
                output (tf.Tensor):
                target (tf.Tensor):

            Returns:
                calculated loss

            """
        try:
            loss = self.calculate_loss(*self.preprocess(output, target,**kwargs))
            loss=self._handel_abnormal(loss)
            loss = self._get_reduction(loss)
            return loss
        except Exception as e:
            print(e)
            PrintException()
            raise e



class CrossEntropyLoss(_ClassificationLoss):
    r"""This criterion combines :func:`nn.LogSoftmax` and :func:`nn.NLLLoss` in one single class.

    It is useful when training a classification problem with `C` classes.
    If provided, the optional argument :attr:`weight` should be a 1D `Tensor`
    assigning weight to each of the classes.
    This is particularly useful when you have an unbalanced training set.

    The `input` is expected to contain raw, unnormalized scores for each class.

    `input` has to be a Tensor of size either :math:`(minibatch, C)` or
    :math:`(minibatch, C, d_1, d_2, ..., d_K)`
    with :math:`K \geq 1` for the `K`-dimensional case (described later).

    This criterion expects a class index in the range :math:`[0, C-1]` as the
    `target` for each value of a 1D tensor of size `minibatch`; if `ignore_index`
    is specified, this criterion also accepts this class index (this index may not
    necessarily be in the class range).

    The loss can be described as:

    .. math::
        \text{loss}(x, class) = -\log\left(\frac{\exp(x[class])}{\sum_j \exp(x[j])}\right)
                       = -x[class] + \log\left(\sum_j \exp(x[j])\right)

    or in the case of the :attr:`weight` argument being specified:

    .. math::
        \text{loss}(x, class) = weight[class] \left(-x[class] + \log\left(\sum_j \exp(x[j])\right)\right)

    The losses are averaged across observations for each minibatch.

    Can also be used for higher dimension inputs, such as 2D images, by providing
    an input of size :math:`(minibatch, C, d_1, d_2, ..., d_K)` with :math:`K \geq 1`,
    where :math:`K` is the number of dimensions, and a target of appropriate shape
    (see below).


    Args:
        axis (int): the position where the classes is.
        sample_weight (Tensor): means the weights of  classes , it shoud be a 1D tensor and length the same as
        number of classes.
        from_logits (bool): whether the output tensor is normalized as a probability (total equal to 1)
        ignore_index (int or list of int):
        cutoff (None or decimal): the cutoff point of probability for classification, should be None of a number
        less than 1..
        is_target_onehot (bool): Is the target tensor in onehot format?
        label_smooth (bool): Should use label smoothing?
        reduction (string): the method to aggrgate loss. None means no need to aggregate, 'mean' means average loss,
            'sum' means the summation of losses,'batch_mean' means average loss cross the batch axis then
            summation them.

    Examples:
    >>> output=to_tensor([[0.1, 0.7 , 0.2],[0.3 , 0.6 , 0.1],[0.9 , 0.05 , 0.05],[0.3 , 0.4 , 0.3]]).float()
    >>> print(output.shape)
    torch.Size([4, 3])
    >>> target=to_tensor([1,0,1,2]).long()
    >>> print(target.shape)
    torch.Size([4])
    >>> CrossEntropyLoss(reduction='mean')(output,target).cpu()
    tensor(1.1305)
    >>> CrossEntropyLoss(reduction='sum')(output,target).cpu()
    tensor(4.5221)
    >>> CrossEntropyLoss(label_smooth=True,reduction='mean')(output,target).cpu()
    tensor(1.0786)
    >>> CrossEntropyLoss(sample_weight=to_tensor([1.0,1.0,0.5]).float(),reduction='mean')(output,target).cpu()
    tensor(0.9889)
    >>> CrossEntropyLoss(ignore_index=2,reduction='mean')(output,target).cpu()
    tensor(0.8259)




    """

    def __init__(self, axis=1, sample_weight=None, from_logits=False, ignore_index=-100, cutoff=None, label_smooth=False, reduction='mean', name='CrossEntropyLoss'):
        """

        Args:
            axis (int): the axis where the class label is.
            sample_weight ():
            from_logits ():
            ignore_index ():
            cutoff ():
            label_smooth ():
            reduction (string):
            name (stringf):
        """
        super().__init__(axis, sample_weight, from_logits, ignore_index, cutoff, label_smooth, reduction, name)
        self._built = True

    def calculate_loss(self, output, target, **kwargs):
        """

        Args:
            output ():
            target ():
            **kwargs ():

        Returns:

        """
        if not self.need_target_onehot:
            if self.is_target_onehot:
                target=argmax(target,self.axis)
            if self.is_logsoftmax == False:
                loss = torch.nn.functional.cross_entropy(output, target, self.sample_weight, ignore_index=self.ignore_index, reduction='none')
            else:
                loss = torch.nn.functional.nll_loss(output, target, self.sample_weight, ignore_index=self.ignore_index, reduction='none')
        else:
            sample_weight=self.sample_weight
            if ndim(output)==2:
                if self.is_logsoftmax == True:
                    sample_weight =self.sample_weight * self.ignore_index_weight
            else:
                reshape_shape = [1] * ndim(output)
                reshape_shape[self.axis] = self.num_classes
                sample_weight=self.sample_weight.view( *reshape_shape)
                if self.is_logsoftmax == True:
                    sample_weight=self.sample_weight.view( *reshape_shape)*self.ignore_index_weight.view( *reshape_shape)
            #-sum([p[i] * log2(q[i]) for i in range(len(p))])
            if self.is_logsoftmax == False:
                loss = -reduce_sum(target * F.log_softmax(output,dim=self.axis)*sample_weight,axis=1)
            else:
                loss = -reduce_sum(target * output * sample_weight,axis=1)
        return loss


class NLLLoss(_ClassificationLoss):
    r"""The negative log likelihood loss. It is useful to train a classification
    problem with `C` classes.

    If provided, the optional argument :attr:`weight` should be a 1D Tensor assigning
    weight to each of the classes. This is particularly useful when you have an
    unbalanced training set.

    The `input` given through a forward call is expected to contain
    log-probabilities of each class. `input` has to be a Tensor of size either
    :math:`(minibatch, C)` or :math:`(minibatch, C, d_1, d_2, ..., d_K)`
    with :math:`K \geq 1` for the `K`-dimensional case (described later).

    Obtaining log-probabilities in a neural network is easily achieved by
    adding a  `LogSoftmax`  layer in the last layer of your network.
    You may use `CrossEntropyLoss` instead, if you prefer not to add an extra
    layer.

    The `target` that this loss expects should be a class index in the range :math:`[0, C-1]`
    where `C = number of classes`; if `ignore_index` is specified, this loss also accepts
    this class index (this index may not necessarily be in the class range).

    The unreduced (i.e. with :attr:`reduction` set to ``'none'``) loss can be described as:

    .. math::
        \ell(x, y) = L = \{l_1,\dots,l_N\}^\top, \quad
        l_n = - w_{y_n} x_{n,y_n}, \quad
        w_{c} = \text{weight}[c] \cdot \mathbb{1}\{c \not= \text{ignore\_index}\},

    where :math:`x` is the input, :math:`y` is the target, :math:`w` is the weight, and
    :math:`N` is the batch size. If :attr:`reduction` is not ``'none'``
    (default ``'mean'``), then

    .. math::
        \ell(x, y) = \begin{cases}
            \sum_{n=1}^N \frac{1}{\sum_{n=1}^N w_{y_n}} l_n, &
            \text{if reduction} = \text{'mean';}\\
            \sum_{n=1}^N l_n,  &
            \text{if reduction} = \text{'sum'.}
        \end{cases}

    Can also be used for higher dimension inputs, such as 2D images, by providing
    an input of size :math:`(minibatch, C, d_1, d_2, ..., d_K)` with :math:`K \geq 1`,
    where :math:`K` is the number of dimensions, and a target of appropriate shape
    (see below). In the case of images, it computes NLL loss per-pixel.

    Args:
        weight (Tensor, optional): a manual rescaling weight given to each
            class. If given, it has to be a Tensor of size `C`. Otherwise, it is
            treated as if having all ones.
        size_average (bool, optional): Deprecated (see :attr:`reduction`). By default,
            the losses are averaged over each loss element in the batch. Note that for
            some losses, there are multiple elements per sample. If the field :attr:`size_average`
            is set to ``False``, the losses are instead summed for each minibatch. Ignored
            when reduce is ``False``. Default: ``True``
        ignore_index (int, optional): Specifies a target value that is ignored
            and does not contribute to the input gradient. When
            :attr:`size_average` is ``True``, the loss is averaged over
            non-ignored targets.
        reduce (bool, optional): Deprecated (see :attr:`reduction`). By default, the
            losses are averaged or summed over observations for each minibatch depending
            on :attr:`size_average`. When :attr:`reduce` is ``False``, returns a loss per
            batch element instead and ignores :attr:`size_average`. Default: ``True``
        reduction (string, optional): Specifies the reduction to apply to the output:
            ``'none'`` | ``'mean'`` | ``'sum'``. ``'none'``: no reduction will be applied,
            ``'mean'``: the sum of the output will be divided by the number of
            elements in the output, ``'sum'``: the output will be summed. Note: :attr:`size_average`
            and :attr:`reduce` are in the process of being deprecated, and in the meantime,
            specifying either of those two args will override :attr:`reduction`. Default: ``'mean'``

    Shape:
        - Input: :math:`(N, C)` where `C = number of classes`, or
          :math:`(N, C, d_1, d_2, ..., d_K)` with :math:`K \geq 1`
          in the case of `K`-dimensional loss.
        - Target: :math:`(N)` where each value is :math:`0 \leq \text{targets}[i] \leq C-1`, or
          :math:`(N, d_1, d_2, ..., d_K)` with :math:`K \geq 1` in the case of
          K-dimensional loss.
        - Output: scalar.
          If :attr:`reduction` is ``'none'``, then the same size as the target: :math:`(N)`, or
          :math:`(N, d_1, d_2, ..., d_K)` with :math:`K \geq 1` in the case
          of K-dimensional loss.

    Examples:
    >>> output=to_tensor([[0.1, 0.7 , 0.2],[0.3 , 0.6 , 0.1],[0.9 , 0.05 , 0.05],[0.3 , 0.4 , 0.3]]).float()
    >>> print(output.shape)
    torch.Size([4, 3])
    >>> target=to_tensor([1,0,1,2]).long()
    >>> print(target.shape)
    torch.Size([4])
    >>> NLLLoss(reduction='mean')(output,target).cpu()
    tensor(-0.3375)
    >>> NLLLoss(reduction='sum')(output,target).cpu()
    tensor(-1.3500)
    >>> NLLLoss(label_smooth=True,reduction='mean')(output,target).cpu()
    tensor(1.1034)
    >>> NLLLoss(loss_weights=to_tensor([1.0,1.0,0.5]).float(),reduction='mean')(output,target).cpu()
    tensor(-0.2625)
    >>> NLLLoss(ignore_index=2,reduction='mean')(output,target).cpu()
    tensor(-0.2625)


    """

    def __init__(self, axis=1, sample_weight=None, from_logits=False, ignore_index=-100, cutoff=None, label_smooth=False, reduction='mean', name='NllLoss'):
        super().__init__(axis, sample_weight, from_logits, ignore_index, cutoff, label_smooth, reduction, name)
        self._built = True

    def calculate_loss(self, output, target, **kwargs):
        """

        Args:
            output ():
            target ():
            **kwargs ():

        Returns:

        """
        reshape_shape = [1] * ndim(output)
        reshape_shape[self.axis] = self.num_classes
        loss_weights = reshape(self.sample_weight, tuple(reshape_shape))

        if self.is_target_onehot and ndim(target) == ndim(output):
            if not self.is_logsoftmax:
                output=log_softmax(output,axis=self.axis)
            loss=-reduce_sum(target * output * loss_weights,axis=1)
        else:
            loss = F.nll_loss(output, target, weight=self.sample_weight, ignore_index=self.ignore_index, reduction='none')
        return loss


class F1ScoreLoss(_ClassificationLoss):
    """
    This operation computes the f-measure between the output and target. If beta is set as one,
    its called the f1-scorce or dice similarity coefficient. f1-scorce is monotonic in jaccard distance.

    f-measure = (1 + beta ** 2) * precision * recall / (beta ** 2 * precision + recall)

    This loss function is frequently used in semantic segmentation of images. Works with imbalanced classes, for
    balanced classes you should prefer cross_entropy instead.
    This operation works with both binary and multiclass classification.

    Args:
        beta: greater than one weights recall higher than precision, less than one for the opposite.
        Commonly chosen values are 0.5, 1 or 2.
        axis (int): the position where the classes is.
        sample_weight (Tensor): means the weights of  classes , it shoud be a 1D tensor and length the same as
        number of classes.
        from_logits (bool): whether the output tensor is normalized as a probability (total equal to 1)
        ignore_index (int or list of int):
        cutoff (None or decimal): the cutoff point of probability for classification, should be None of a number
        less than 1..
        is_target_onehot (bool): Is the target tensor in onehot format?
        label_smooth (bool): Should use label smoothing?
        reduction (string): the method to aggrgate loss. None means no need to aggregate, 'mean' means average loss,
            'sum' means the summation of losses,'batch_mean' means average loss cross the batch axis then
            summation them.

    Returns:
        :class:`~cntk.ops.functions.Function`

    Examples:
    >>> output=to_tensor([[0.1, 0.7 , 0.2],[0.3 , 0.6 , 0.1],[0.9 , 0.05 , 0.05],[0.3 , 0.4 , 0.3]]).float()
    >>> print(output.shape)
    torch.Size([4, 3])
    >>> target=to_tensor([1,0,1,2]).long()
    >>> print(target.shape)
    torch.Size([4])
    >>> F1ScoreLoss(reduction='mean')(output,target).cpu()
    tensor(0.6670)
    >>> F1ScoreLoss(reduction='sum')(output,target).cpu()
    tensor(2.6680)
    >>> F1ScoreLoss(label_smooth=True,reduction='mean')(output,target).cpu()
    tensor(0.6740)
    >>> F1ScoreLoss(loss_weights=to_tensor([1.0,1.0,0]).float(),reduction='mean')(output,target).cpu()
    tensor(0.6670)
    >>> F1ScoreLoss(ignore_index=2,reduction='mean')(output,target).cpu()
    tensor(0.6670)



    """

    def __init__(self, beta=1, axis=1, sample_weight=None, from_logits=False, ignore_index=-100, cutoff=None, label_smooth=False, reduction='mean', name='CrossEntropyLoss'):
        super().__init__(axis, sample_weight, from_logits, ignore_index, cutoff, label_smooth, reduction, name)
        self.beta = beta
        self.need_target_onehot = True
        self._built = True

    def calculate_loss(self, output, target, **kwargs):
        _dtype = output.dtype
        if self.is_logsoftmax:
            output = clip(exp(output), 1e-8, 1 - 1e-8)
            self.from_logits = True
        if self.from_logits == False:
            output = softmax(output, self.axis)
        if target.dtype == torch.int64 or self.is_target_onehot == False:
            target = make_onehot(target, self.num_classes, axis=1).to(_dtype)
        target.require_grads = False
        tp = (target * output).sum()
        tn = ((1 - target) * (1 - output)).sum()
        fp = ((1 - target) * output).sum()
        fn = (target * (1 - output)).sum()
        precision = tp / (tp + fp + epsilon())
        recall = tp / (tp + fn + epsilon())
        return 1 - (1 + self.beta ** 2) * precision * recall / (self.beta ** 2 * precision + recall)


class FocalLoss(_ClassificationLoss):
    """
    Compute binary focal loss between target and output logits.
    See :class:`~pytorch_toolbelt.losses.FocalLoss` for details.

    Args:
        axis (int): the position where the classes is.
        sample_weight (Tensor): means the weights of  classes , it shoud be a 1D tensor and length the same as
        number of classes.
        from_logits (bool): whether the output tensor is normalized as a probability (total equal to 1)
        ignore_index (int or list of int):
        cutoff (None or decimal): the cutoff point of probability for classification, should be None of a number
        less than 1..
        is_target_onehot (bool): Is the target tensor in onehot format?
        label_smooth (bool): Should use label smoothing?
        reduction (string): the method to aggrgate loss. None means no need to aggregate, 'mean' means average loss,
            'sum' means the summation of losses,'batch_mean' means average loss cross the batch axis then
            summation them.


    References::

        https://github.com/open-mmlab/mmdetection/blob/master/mmdet/core/loss/losses.py

        normalized (bool): Compute normalized focal loss (https://arxiv.org/pdf/1909.07829.pdf).
        threshold (float, optional): Compute reduced focal loss (https://arxiv.org/abs/1903.01347).


    """

    def __init__(self, alpha=0.5, gamma=2, normalized=False, threshold=None, axis=1, sample_weight=None, from_logits=False, ignore_index=-100, cutoff=None,
                 label_smooth=False, reduction='mean', name='FocalLoss'):
        super().__init__(axis, sample_weight, from_logits, ignore_index, cutoff, label_smooth, reduction, name)
        self.alpha = alpha
        self.gamma = gamma
        self.threshold = threshold
        self.normalized = normalized

    def calculate_loss(self, output, target, **kwargs):
        """


        Args:
            output: Tensor of arbitrary shape
            target: Tensor of the same shape as input



        Returns:

            """
        if not self.need_target_onehot:
            if self.is_target_onehot:
                target=argmax(target,self.axis)
            if self.is_logsoftmax == False:
                output=torch.log_softmax(output,self.axis)
            probs = exp(output)
            return F.nll_loss( pow( 1 - probs, self.gamma)*output, target ,self.sample_weight, ignore_index=self.ignore_index, reduction='none')
        else:
            sample_weight = self.sample_weight
            if ndim(output) == 2:
                if self.is_logsoftmax == True:
                    sample_weight = self.sample_weight * self.ignore_index_weight
            else:
                reshape_shape = [1] * ndim(output)
                reshape_shape[self.axis] = self.num_classes
                sample_weight = self.sample_weight.view(*reshape_shape)
                if self.is_logsoftmax == True:
                    sample_weight = self.sample_weight.view(*reshape_shape) * self.ignore_index_weight.view(*reshape_shape)

            if self.is_logsoftmax == False:
                    output=torch.log_softmax(output,self.axis)
            probs = exp(output)*sample_weight
            loss = -self.alpha *target* pow((1-probs), self.gamma)*output
            return loss


        #- \alpha(1 - softmax(x)[class ]) ^ gamma \log(softmax(x)[class])

        #
        # if self.is_logsoftmax:
        #     output = clip(exp(output), 1e-8, 1 - 1e-8)
        # logpt = -F.cross_entropy(output, target, weight=self.sample_weight, ignore_index=self.ignore_index, reduction="none")
        # pt = clip(exp(logpt), 1e-8, 1 - 1e-8)
        #
        # # compute the loss
        # if self.threshold is None or self.threshold == 0:
        #     focal_term = (1 - pt).pow(self.gamma)
        # else:
        #     focal_term = ((1.0 - pt) / self.threshold).pow(self.gamma)
        #     focal_term[pt < self.threshold] = 1
        #
        # loss = -focal_term * logpt
        #
        # if self.alpha is not None:
        #     loss = loss * (self.alpha * target + (1 - self.alpha) * (1 - target))
        # if self.normalized:
        #     norm_factor = sum(focal_term)
        #     loss = loss / norm_factor
        #
        # return loss


class BCELoss(_ClassificationLoss):
    def __init__(self, axis=1, sample_weight=None, from_logits=False, ignore_index=-100, cutoff=None, label_smooth=False, reduction='mean', name='BCELoss'):
        super().__init__(axis, sample_weight, from_logits, ignore_index, cutoff, label_smooth, reduction, name)
        self._built = True
        self.num_classes = 1
        self.is_logsoftmax = False
        self.need_target_onehot = False
        self.is_target_onehot = False

    def calculate_loss(self, output, target, **kwargs):
        """

        Args:
            output ():
            target ():
            **kwargs ():

        Returns:

        """

        loss = F.binary_cross_entropy_with_logits(output, target, weight=self.sample_weight, reduction='none')
        return loss


class DiceLoss(_ClassificationLoss):
    r"""This criterion combines :func:`nn.LogSoftmax` and :func:`nn.NLLLoss` in one single class.

    It is useful when training a classification problem with `C` classes.
    If provided, the optional argument :attr:`weight` should be a 1D `Tensor`
    assigning weight to each of the classes.
    This is particularly useful when you have an unbalanced training set.

    Args:
        axis (int): the position where the classes is.
        sample_weight (Tensor): means the weights of  classes , it shoud be a 1D tensor and length the same as
        number of classes.
        from_logits (bool): whether the output tensor is normalized as a probability (total equal to 1)
        ignore_index (int or list of int):
        cutoff (None or decimal): the cutoff point of probability for classification, should be None of a number
        less than 1..
        label_smooth (bool): Should use label smoothing?
        reduction (string): the method to aggrgate loss. None means no need to aggregate, 'mean' means average loss,
            'sum' means the summation of losses,'batch_mean' means average loss cross the batch axis then
            summation them.

    Examples:
    >>> output=zeros((1,3,128,128))
    >>> output[0,1,32:64,48:92]=1
    >>> output[0,2,12:32,56:64]=1
    >>> target=zeros((1,128,128)).long()
    >>> target[0,33:63,50:9]=1
    >>> target[0,13:35,52:65]=2
    >>> DiceLoss(reduction='mean')(output,target).cpu()
    tensor(0.8271)
    >>> DiceLoss(ignore_index=0,reduction='mean')(output,target).cpu()
    tensor(0.9829)



    Reference:
        https://arxiv.org/abs/1707.03237


    """

    def __init__(self, smooth=1., axis=1, sample_weight=None, from_logits=False, ignore_index=-100, cutoff=None, label_smooth=False, reduction='mean', name='DiceLoss'):
        """
        Args:
            axis (int): the axis where the class label is.
            sample_weight ():
            from_logits ():
            ignore_index ():
            cutoff ():
            label_smooth ():
            reduction (string):
            name (stringf):
        """

        super().__init__(axis, sample_weight, from_logits, ignore_index, cutoff, label_smooth, reduction, name)
        self.smooth = smooth
        self.is_logsoftmax = False
        self.need_target_onehot = True
        self.is_multiselection = False
        self._built = True

    def calculate_loss(self, output, target, **kwargs):
        """

        Args:
            output ():
            target ():
            **kwargs ():

        Returns:

        """
        if self.is_logsoftmax:
            output=exp(output)
        reduce_axes=list(range(target.ndim))
        axis=self.axis if self.axis>=0 else target.ndim+self.axis
        reduce_axes.remove(0)
        reduce_axes.remove(axis)
        loss_weights=self.sample_weight.clone().to(get_device())
        # for k in range(target.ndim-self.loss_weights.ndim):
        #     loss_weights=loss_weights.expand_dims(0)
        intersection = reduce_sum(target * output, axis=reduce_axes)*loss_weights
        den1 = reduce_sum(output, axis=reduce_axes)*loss_weights
        den2 = reduce_sum(target, axis=reduce_axes)*loss_weights
        dice = 1.0 - ((2.0 * intersection + self.smooth) / (den1 + den2 + self.smooth))
        return dice








class L1Loss(_PairwiseLoss):
    r"""l1_loss(input, target, size_average=None, reduce=None, reduction='mean') -> Tensor

     Function that takes the mean element-wise absolute value difference.

     See :class:`~torch.nn.L1Loss` for details.
     """
    def __init__(self, reduction='mean', name='L1Loss'):
        super(L1Loss, self).__init__(reduction)
        self.name = name
        self.reduction = reduction

    def calculate_loss(self, output, target, **kwargs):
        """

        Args:
            output ():
            target ():
            **kwargs ():

        Returns:

        """

        return F.l1_loss(output, target, reduction='none')



class L2Loss(_PairwiseLoss):
    r"""mse_loss(input, target, size_average=None, reduce=None, reduction='mean') -> Tensor

        Measures the element-wise mean squared error.

        See :class:`~torch.nn.MSELoss` for details.
        """
    def __init__(self, reduction='mean', name='MSELoss'):
        super(L2Loss, self).__init__(reduction)
        self.name = name
        self.reduction = reduction

    def calculate_loss(self, output, target, **kwargs):
        """

        Args:
            output ():
            target ():
            **kwargs ():

        Returns:

        """
        return 0.5 * F.mse_loss(output, target, reduction='none')



class SmoothL1Loss(_PairwiseLoss):
    r"""Function that uses a squared term if the absolute
    element-wise error falls below 1 and an L1 term otherwise.

    See :class:`~torch.nn.SmoothL1Loss` for details.
    """
    def __init__(self, reduction='mean', name='SmoothL1Loss'):
        super(SmoothL1Loss, self).__init__(reduction=reduction)
        self.name = name
        self.reduction = reduction
        self.huber_delta = 0.5

    def calculate_loss(self, output, target, **kwargs):
        """

        Args:
            output ():
            target ():
            **kwargs ():

        Returns:

        """
        return F.smooth_l1_loss(output, target, reduction='none')



class MSELoss(_PairwiseLoss):
    r"""mse_loss(input, target, size_average=None, reduce=None, reduction='mean') -> Tensor

        Measures the element-wise mean squared error.

        See :class:`~torch.nn.MSELoss` for details.
        """
    def __init__(self, reduction='mean', name='MSELoss'):
        super(MSELoss, self).__init__(reduction=reduction)
        self.name = name
        self.reduction = reduction

    def calculate_loss(self, output, target, **kwargs):
        """

        Args:
            output ():
            target ():
            **kwargs ():

        Returns:

        """
        return F.mse_loss(output, target, reduction='none')


class WingLoss(_PairwiseLoss):
    def __init__(self, omega=10, epsilon=2, name='WingLoss'):
        super(WingLoss, self).__init__()
        self.name = name
        self.omega = omega
        self.epsilon = epsilon

    def calculate_loss(self, output, target, **kwargs):
        """

        Args:
            output ():
            target ():
            **kwargs ():

        Returns:

        """
        delta_y = (target - output).abs()
        delta_y1 = delta_y[delta_y < self.omega]
        delta_y2 = delta_y[delta_y >= self.omega]
        loss1 = self.omega * torch.log(1 + delta_y1 / self.epsilon)
        C = self.omega - self.omega * math.log(1 + self.omega / self.epsilon)
        loss2 = delta_y2 - C
        return (loss1.sum() + loss2.sum()) / (len(loss1) + len(loss2))


class AdaptiveWingLoss(_PairwiseLoss):
    def __init__(self, omega=14, theta=0.5, epsilon=1, alpha=2.1, name='AdaptiveWingLoss'):
        super(AdaptiveWingLoss, self).__init__()
        self.name = name
        self.omega = omega
        self.theta = theta
        self.epsilon = epsilon
        self.alpha = alpha

    def calculate_loss(self, output, target, **kwargs):
        """

        Args:
            output ():
            target ():
            **kwargs ():

        Returns:

        """
        y = target
        y_hat = output
        delta_y = (y - y_hat).abs()
        delta_y1 = delta_y[delta_y < self.theta]
        delta_y2 = delta_y[delta_y >= self.theta]
        y1 = y[delta_y < self.theta]
        y2 = y[delta_y >= self.theta]
        loss1 = self.omega * torch.log(1 + torch.pow(delta_y1 / self.omega, self.alpha - y1))
        A = self.omega * (1 / (1 + torch.pow(self.theta / self.epsilon, self.alpha - y2))) * (self.alpha - y2) * (
            torch.pow(self.theta / self.epsilon, self.alpha - y2 - 1)) * (1 / self.epsilon)
        C = self.theta * A - self.omega * torch.log(1 + torch.pow(self.theta / self.epsilon, self.alpha - y2))
        loss2 = A * delta_y2 - C
        return (loss1.sum() + loss2.sum()) / (len(loss1) + len(loss2))


class ExponentialLoss(_PairwiseLoss):
    def __init__(self, reduction='mean', name='ExponentialLoss'):
        super(ExponentialLoss, self).__init__(reduction=reduction)
        self.name = name
        self.reduction = reduction

    def calculate_loss(self, output, target, **kwargs):
        """

        Args:
            output ():
            target ():
            **kwargs ():

        Returns:

        """

        if output.shape == target.shape:
            error = (output - target) ** 2
            loss = 1 - (-1 * error / np.pi).exp()
            if self.reduction == "mean":
                loss = loss.mean()
            if self.reduction == "sum":
                loss = loss.sum()
            if self.reduction == "batchwise_mean":
                loss = loss.mean(0).sum()
            return loss
        else:
            raise ValueError('output and target shape should the same in ExponentialLoss. ')


class ItakuraSaitoLoss(_PairwiseLoss):
    def __init__(self, reduction='mean', name='ItakuraSaitoLoss'):
        super(ItakuraSaitoLoss, self).__init__(reduction=reduction)
        self.name = name
        self.reduction = reduction

    def calculate_loss(self, output, target, **kwargs):
        """

        Args:
            output ():
            target ():
            **kwargs ():

        Returns:

        """
        #  y_true/(y_pred+1e-12) - log(y_true/(y_pred+1e-12)) - 1;
        if output.shape == target.shape:
            if -1 <= output.min() < 0 and -1 <= target.min() < 0:
                output = output + 1
                target = target + 1
            loss = (target / (output + 1e-8)) - ((target + 1e-8) / (output + 1e-8)).log() - 1
            if self.reduction == "mean":
                loss = loss.mean()
            if self.reduction == "sum":
                loss = loss.sum()
            if self.reduction == "batchwise_mean":
                loss = loss.mean(0).sum()
            return loss
        else:
            raise ValueError('output and target shape should the same in ItakuraSaitoLoss. ')


class CosineSimilarityLoss(_PairwiseLoss):
    def __init__(self, ):
        super(CosineSimilarityLoss, self).__init__()

    def calculate_loss(self, output, target, **kwargs):
        """

        Args:
            output ():
            target ():
            **kwargs ():

        Returns:

        """
        return 1.0 - torch.cosine_similarity(output, target).mean()


def gaussian(window_size, sigma=1.5):
    gauss = torch.Tensor([exp(-(x - window_size // 2) ** 2 / float(2 * sigma ** 2)) for x in range(window_size)])
    return gauss / gauss.sum()


def create_window(window_size, channel):
    _1D_window = gaussian(window_size, 1.5).unsqueeze(1)
    _2D_window = _1D_window.mm(_1D_window.t()).float().unsqueeze(0).unsqueeze(0)
    window = _2D_window.expand(channel, 1, window_size, window_size).contiguous()
    return window


def ssim(img1, img2, window_size=11, window=None, full=False, val_range=None):
    # Value range can be different from 255. Other common ranges are 1 (sigmoid) and 2 (tanh).
    if val_range is None:
        if torch.max(img1) > 128:
            max_val = 255
        else:
            max_val = 1

        if torch.min(img1) < -0.5:
            min_val = -1
        else:
            min_val = 0
        L = max_val - min_val
    else:
        L = val_range

    padd = 0
    (_, channel, height, width) = img1.size()
    if window is None:
        real_size = min(window_size, height, width)
        window = create_window(real_size, channel=channel).to(img1.device)

    mu1 = F.conv2d(img1, window, padding=padd, groups=channel)
    mu2 = F.conv2d(img2, window, padding=padd, groups=channel)

    mu1_sq = mu1.pow(2)
    mu2_sq = mu2.pow(2)
    mu1_mu2 = mu1 * mu2

    sigma1_sq = F.conv2d(img1 * img1, window, padding=padd, groups=channel) - mu1_sq
    sigma2_sq = F.conv2d(img2 * img2, window, padding=padd, groups=channel) - mu2_sq
    sigma12 = F.conv2d(img1 * img2, window, padding=padd, groups=channel) - mu1_mu2

    C1 = (0.01 * L) ** 2
    C2 = (0.03 * L) ** 2

    v1 = 2.0 * sigma12 + C2
    v2 = sigma1_sq + sigma2_sq + C2
    cs = torch.mean(v1 / v2)  # contrast sensitivity

    ssim_map = ((2 * mu1_mu2 + C1) * v1) / ((mu1_sq + mu2_sq + C1) * v2)

    ret = ssim_map.mean()

    if full:
        return ret, cs
    return ret


def msssim(img1, img2, window_size=11, val_range=None, normalize=False):
    device = img1.device
    weights = torch.FloatTensor([0.0448, 0.2856, 0.3001, 0.2363, 0.1333]).to(device)
    levels = weights.size()[0]
    mssim = []
    mcs = []
    for _ in range(levels):
        sim, cs = ssim(img1, img2, window_size=window_size, full=True, val_range=val_range)
        mssim.append(sim)
        mcs.append(cs)

        img1 = F.avg_pool2d(img1, (2, 2))
        img2 = F.avg_pool2d(img2, (2, 2))

    mssim = torch.stack(mssim)
    mcs = torch.stack(mcs)

    # Normalize (to avoid NaNs during training unstable models, not compliant with original definition)
    if normalize:
        mssim = (mssim + 1) / 2
        mcs = (mcs + 1) / 2

    pow1 = mcs ** weights
    pow2 = mssim ** weights
    # From Matlab implementation https://ece.uwaterloo.ca/~z70wang/research/iwssim/
    output = torch.prod(pow1[:-1] * pow2[-1])
    return output


class MS_SSIMLoss(_Loss):
    def __init__(self, reduction='mean', window_size=11, max_val=255):
        super(MS_SSIMLoss, self).__init__()
        self.reduction = reduction
        self.window_size = window_size
        self.channel = 3

    def forward(self, output, target) -> 'loss':
        return 1 - msssim(output, target, window_size=self.window_size, normalize=True)


class IoULoss(_Loss):
    def __init__(self, ignore_index=-1000, reduction='mean'):
        super(IoULoss, self).__init__(reduction=reduction)
        self.ignore_index = ignore_index

    def forward(self, output, target) -> 'loss':
        output = argmax(output, 1)
        output_flat = output.reshape(-1)
        target_flat = target.reshape(-1)
        output_flat = output_flat[target_flat != self.ignore_index]
        target_flat = target_flat[target_flat != self.ignore_index]
        output_flat = output_flat[target_flat != 0]
        target_flat = target_flat[target_flat != 0]
        intersection = (output_flat == target_flat).float().sum()
        union = ((output_flat + target_flat) > 0).float().sum().clamp(min=1)
        loss = -(intersection / union).log()
        return loss


class SoftIoULoss(_Loss):
    def __init__(self, n_classes, reduction="mean", reduced=False):
        super(SoftIoULoss, self).__init__()
        self.n_classes = n_classes
        self.reduction = reduction
        self.reduced = reduced

    def forward(self, output, target) -> 'loss':
        # logit => N x Classes x H x W
        # target => N x H x W

        N = len(output)

        pred = F.softmax(output, dim=1)
        target_onehot = make_onehot(target, self.n_classes)

        # Numerator Product
        inter = pred * target_onehot
        # Sum over all pixels N x C x H x W => N x C
        inter = inter.view(N, self.n_classes, -1).sum(2)

        # Denominator
        union = pred + target_onehot - (pred * target_onehot)
        # Sum over all pixels N x C x H x W => N x C
        union = union.view(N, self.n_classes, -1).sum(2)

        loss = inter / (union + 1e-16)

        # Return average loss over classes and batch
        return -loss.mean()


def _lovasz_grad(gt_sorted):
    """
    Computes gradient of the Lovasz extension w.r.t sorted errors
    See Alg. 1 in paper
    """
    p = len(gt_sorted)
    gts = gt_sorted.sum()
    intersection = gts - gt_sorted.float().cumsum(0)
    union = gts + (1 - gt_sorted).float().cumsum(0)
    jaccard = 1. - intersection / union
    if p > 1:  # cover 1-pixel case
        jaccard[1:p] = jaccard[1:p] - jaccard[0:-1]
    return jaccard


class LovaszSoftmax(_Loss):
    def __init__(self, reduction="mean", reduced=False):
        super(LovaszSoftmax, self).__init__()
        self.reduction = reduction
        self.reduced = reduced
        self.num_classes = None

    def prob_flatten(self, input, target):
        assert input.dim() in [4, 5]
        num_class = input.size(1)
        if input.dim() == 4:
            input = input.permute(0, 2, 3, 1).contiguous()
            input_flatten = input.view(-1, num_class)
        elif input.dim() == 5:
            input = input.permute(0, 2, 3, 4, 1).contiguous()
            input_flatten = input.view(-1, num_class)
        target_flatten = target.view(-1)
        return input_flatten, target_flatten

    def lovasz_softmax_flat(self, output, target):
        num_classes = output.size(1)
        losses = []
        for c in range(num_classes):
            target_c = (target == c).float()
            if num_classes == 1:
                input_c = output[:, 0]
            else:
                input_c = output[:, c]
            loss_c = (torch.autograd.Variable(target_c) - input_c).abs()
            loss_c_sorted, loss_index = torch.sort(loss_c, 0, descending=True)
            target_c_sorted = target_c[loss_index]
            losses.append(torch.dot(loss_c_sorted, torch.autograd.Variable(_lovasz_grad(target_c_sorted))))
        losses = torch.stack(losses)

        if self.reduction == 'none':
            loss = losses
        elif self.reduction == 'sum':
            loss = losses.sum()
        else:
            loss = losses.mean()
        return loss

    def flatten_binary_scores(self, scores, labels, ignore=None):
        """
        Flattens predictions in the batch (binary case)
        Remove labels equal to 'ignore'
        """
        scores = scores.view(-1)
        labels = labels.view(-1)
        if ignore is None:
            return scores, labels
        valid = (labels != ignore)
        vscores = scores[valid]
        vlabels = labels[valid]
        return vscores, vlabels

    def lovasz_hinge(self, logits, labels, per_image=True, ignore=None):
        """
        Binary Lovasz hinge loss
          logits: [B, H, W] Variable, logits at each pixel (between -\infty and +\infty)
          labels: [B, H, W] Tensor, binary ground truth masks (0 or 1)
          per_image: compute the loss per image instead of per batch
          ignore: void class id

        """
        if per_image:
            loss = (self.lovasz_hinge_flat(*self.flatten_binary_scores(log.unsqueeze(0), lab.unsqueeze(0), ignore)) for
                    log, lab in zip(logits, labels)).mean()
        else:
            loss = self.lovasz_hinge_flat(*self.flatten_binary_scores(logits, labels, ignore))
        return loss

    def lovasz_hinge_flat(self, logits, labels):
        """
        Binary Lovasz hinge loss
          logits: [P] Variable, logits at each prediction (between -\infty and +\infty)
          labels: [P] Tensor, binary ground truth labels (0 or 1)
          ignore: label to ignore

        """
        if len(labels) == 0:
            # only void pixels, the gradients should be 0
            return logits.sum() * 0.
        signs = 2. * labels.float() - 1.
        errors = (1. - logits * torch.tensor(signs, requires_grad=True))
        errors_sorted, perm = torch.sort(errors, dim=0, descending=True)
        perm = perm.data
        gt_sorted = labels[perm]
        grad = _lovasz_grad(gt_sorted)
        loss = torch.dot(F.relu(errors_sorted), grad)
        return loss

    def forward(self, output, target) -> 'loss':
        # print(output.shape, target.shape) # (batch size, class_num, x,y,z), (batch size, 1, x,y,z)
        self.num_classes = output.size(1)
        output, target = self.prob_flatten(output, target)

        # print(output.shape, target.shape)

        losses = self.lovasz_softmax_flat(output, target) if self.num_classes > 2 else self.lovasz_hinge_flat(output,
                                                                                                              target)
        return losses


class TripletLoss(_Loss):
    r"""Creates a criterion that measures the triplet loss given an input
    tensors :math:`x1`, :math:`x2`, :math:`x3` and a margin with a value greater than :math:`0`.
    This is used for measuring a relative similarity between samples. A triplet
    is composed by `a`, `p` and `n` (i.e., `anchor`, `positive examples` and `negative
    examples` respectively). The shapes of all input tensors should be
    :math:`(N, D)`.

    The distance swap is described in detail in the paper `Learning shallow
    convolutional feature descriptors with triplet losses`_ by
    V. Balntas, E. Riba et al.

    The loss function for each sample in the mini-batch is:

    .. math::
        L(a, p, n) = \max \{d(a_i, p_i) - d(a_i, n_i) + {\rm margin}, 0\}


    where

    .. math::
        d(x_i, y_i) = \left\lVert {\bf x}_i - {\bf y}_i \right\rVert_p

    Args:
        margin (float, optional): Default: :math:`1`.
        p (int, optional): The norm degree for pairwise distance. Default: :math:`2`.
        swap (bool, optional): The distance swap is described in detail in the paper
            `Learning shallow convolutional feature descriptors with triplet losses` by
            V. Balntas, E. Riba et al. Default: ``False``.
        size_average (bool, optional): Deprecated (see :attr:`reduction`). By default,
            the losses are averaged over each loss element in the batch. Note that for
            some losses, there are multiple elements per sample. If the field :attr:`size_average`
            is set to ``False``, the losses are instead summed for each minibatch. Ignored
            when reduce is ``False``. Default: ``True``
        reduce (bool, optional): Deprecated (see :attr:`reduction`). By default, the
            losses are averaged or summed over observations for each minibatch depending
            on :attr:`size_average`. When :attr:`reduce` is ``False``, returns a loss per
            batch element instead and ignores :attr:`size_average`. Default: ``True``
        reduction (string, optional): Specifies the reduction to apply to the output:
            ``'none'`` | ``'mean'`` | ``'sum'``. ``'none'``: no reduction will be applied,
            ``'mean'``: the sum of the output will be divided by the number of
            elements in the output, ``'sum'``: the output will be summed. Note: :attr:`size_average`
            and :attr:`reduce` are in the process of being deprecated, and in the meantime,
            specifying either of those two args will override :attr:`reduction`. Default: ``'mean'``

    Shape:
        - Input: :math:`(N, D)` where :math:`D` is the vector dimension.
        - Output: scalar. If :attr:`reduction` is ``'none'``, then :math:`(N)`.

    >>> triplet_loss = nn.TripletMarginLoss(margin=1.0, p=2)
    >>> anchor = torch.randn(100, 128, requires_grad=True)
    >>> positive = torch.randn(100, 128, requires_grad=True)
    >>> negative = torch.randn(100, 128, requires_grad=True)
    >>> output = triplet_loss(anchor, positive, negative)
    >>> output.backward()

    .. _Learning shallow convolutional feature descriptors with triplet losses:
        http://www.bmva.org/bmvc/2016/papers/paper119/index.html
    """
    __constants__ = ['margin', 'p', 'eps', 'swap', 'reduction']
    margin: float
    p: float
    eps: float
    swap: bool

    def __init__(self, margin: float = 1.0, p: float = 2., eps: float = 1e-6, swap: bool = False, reduction: str = 'mean'):
        super(TripletLoss, self).__init__(reduction=reduction)
        self.margin = margin
        self.p = p
        self.eps = eps
        self.swap = swap

    def forward(self, anchor, positive, negative):
        return F.triplet_margin_loss(anchor, positive, negative, margin=self.margin, p=self.p,
                                     eps=self.eps, swap=self.swap, reduction=self.reduction)


TripletMarginLoss = TripletLoss


class HardTripletLoss(_Loss):
    """Hard/Hardest Triplet Loss
    (pytorch implementation of https://omoindrot.github.io/triplet-loss)

    For each anchor, we get the hardest positive and hardest negative to form a triplet.
    """

    def __init__(self, margin=0.1, hardest=False, squared=False):
        """
        Args:
            margin: margin for triplet loss
            hardest: If true, loss is considered only hardest triplets.
            squared: If true, output is the pairwise squared euclidean distance matrix.
                If false, output is the pairwise euclidean distance matrix.
        """
        super(HardTripletLoss, self).__init__()
        self.margin = margin
        self.hardest = hardest
        self.squared = squared

    def _pairwise_distance(self, x, squared=False, eps=1e-16):
        # Compute the 2D matrix of distances between all the embeddings.

        cor_mat = torch.matmul(x, x.t())
        norm_mat = cor_mat.diag()
        distances = norm_mat.unsqueeze(1) - 2 * cor_mat + norm_mat.unsqueeze(0)
        distances = F.relu(distances)

        if not squared:
            mask = torch.eq(distances, 0.0).float()
            distances = distances + mask * eps
            distances = torch.sqrt(distances)
            distances = distances * (1.0 - mask)

        return distances

    def _get_anchor_positive_triplet_mask(self, labels):
        # Return a 2D mask where mask[a, p] is True iff a and p are distinct and have same label.

        device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

        indices_not_equal = torch.eye(labels.shape[0]).to(device).byte() ^ 1

        # Check if labels[i] == labels[j]
        labels_equal = torch.unsqueeze(labels, 0) == torch.unsqueeze(labels, 1)

        mask = indices_not_equal * labels_equal

        return mask

    def _get_anchor_negative_triplet_mask(self, labels):
        # Return a 2D mask where mask[a, n] is True iff a and n have distinct labels.

        # Check if labels[i] != labels[k]
        labels_equal = torch.unsqueeze(labels, 0) == torch.unsqueeze(labels, 1)
        mask = labels_equal ^ 1

        return mask

    def _get_triplet_mask(self, labels):
        """Return a 3D mask where mask[a, p, n] is True iff the triplet (a, p, n) is valid.

        A triplet (i, j, k) is valid if:
            - i, j, k are distinct
            - labels[i] == labels[j] and labels[i] != labels[k]
        """
        device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

        # Check that i, j and k are distinct
        indices_not_same = torch.eye(labels.shape[0]).to(device).byte() ^ 1
        i_not_equal_j = torch.unsqueeze(indices_not_same, 2)
        i_not_equal_k = torch.unsqueeze(indices_not_same, 1)
        j_not_equal_k = torch.unsqueeze(indices_not_same, 0)
        distinct_indices = i_not_equal_j * i_not_equal_k * j_not_equal_k

        # Check if labels[i] == labels[j] and labels[i] != labels[k]
        label_equal = torch.eq(torch.unsqueeze(labels, 0), torch.unsqueeze(labels, 1))
        i_equal_j = torch.unsqueeze(label_equal, 2)
        i_equal_k = torch.unsqueeze(label_equal, 1)
        valid_labels = i_equal_j * (i_equal_k ^ 1)

        mask = distinct_indices * valid_labels  # Combine the two masks

        return mask

    def forward(self, embeddings, labels):
        """
        Args:
            labels: labels of the batch, of size (batch_size,)
            embeddings: tensor of shape (batch_size, embed_dim)

        Returns:
            triplet_loss: scalar tensor containing the triplet loss
        """
        pairwise_dist = self._pairwise_distance(embeddings, squared=self.squared)

        if self.hardest:
            # Get the hardest positive pairs
            mask_anchor_positive = self._get_anchor_positive_triplet_mask(labels).float()
            valid_positive_dist = pairwise_dist * mask_anchor_positive
            hardest_positive_dist, _ = torch.max(valid_positive_dist, dim=1, keepdim=True)

            # Get the hardest negative pairs
            mask_anchor_negative = self._get_anchor_negative_triplet_mask(labels).float()
            max_anchor_negative_dist, _ = torch.max(pairwise_dist, dim=1, keepdim=True)
            anchor_negative_dist = pairwise_dist + max_anchor_negative_dist * (1.0 - mask_anchor_negative)
            hardest_negative_dist, _ = torch.min(anchor_negative_dist, dim=1, keepdim=True)

            # Combine biggest d(a, p) and smallest d(a, n) into final triplet loss
            triplet_loss = F.relu(hardest_positive_dist - hardest_negative_dist + 0.1)
            triplet_loss = torch.mean(triplet_loss)
        else:
            anc_pos_dist = pairwise_dist.unsqueeze(dim=2)
            anc_neg_dist = pairwise_dist.unsqueeze(dim=1)

            # Compute a 3D tensor of size (batch_size, batch_size, batch_size)
            # triplet_loss[i, j, k] will contain the triplet loss of anc=i, pos=j, neg=k
            # Uses broadcasting where the 1st argument has shape (batch_size, batch_size, 1)
            # and the 2nd (batch_size, 1, batch_size)
            loss = anc_pos_dist - anc_neg_dist + self.margin

            mask = self._get_triplet_mask(labels).float()
            triplet_loss = loss * mask

            # Remove negative losses (i.e. the easy triplets)
            triplet_loss = F.relu(triplet_loss)

            # Count number of hard triplets (where triplet_loss > 0)
            hard_triplets = torch.gt(triplet_loss, 1e-16).float()
            num_hard_triplets = torch.sum(hard_triplets)

            triplet_loss = torch.sum(triplet_loss) / (num_hard_triplets + 1e-16)

        return triplet_loss


class CenterLoss(_Loss):
    """Center loss.
    Reference:
    Wen et al. A Discriminative Feature Learning Approach for Deep Face Recognition. ECCV 2016.

    Args:
        num_classes (int): number of classes.
        feat_dim (int): feature dimension.
    """

    def __init__(self, num_classes=751, feat_dim=2048, reduction="mean", reduced=False):
        super(CenterLoss, self).__init__()
        self.reduction = reduction
        self.reduced = reduced
        self.num_classes = num_classes
        self.feat_dim = feat_dim
        self.centers = nn.Parameter(torch.randn(self.num_classes, self.feat_dim))
        init.kaiming_uniform_(self.centers, a=sqrt(5))
        self.to(_device)

    def forward(self, output, target) -> 'loss':
        """
        Args:
            output: feature matrix with shape (batch_size, feat_dim).
            target: ground truth labels with shape (num_classes).

        """
        assert output.size(0) == target.size(0), "features.size(0) is not equal to labels.size(0)"
        batch_size = output.size(0)
        distmat = torch.pow(output, 2).sum(dim=1, keepdim=True).expand(batch_size, self.num_classes) + torch.pow(
            self.centers, 2).sum(dim=1, keepdim=True).expand(self.num_classes, batch_size).t()
        distmat.addmm_(1, -2, output, self.centers.t())
        classes = torch.arange(self.num_classes).long().to(_device)

        target = target.unsqueeze(1).expand(batch_size, self.num_classes)
        mask = target.eq(classes.expand(batch_size, self.num_classes))
        dist = []
        for i in range(batch_size):
            value = distmat[i][mask[i]]
            value = value.clamp(min=1e-8, max=1e+5)  # for numerical stability
            dist.append(value)
        dist = torch.cat(dist)
        if self.reduction == 'mean':
            return dist.mean() / self.num_classes
        else:
            return dist.sum() / self.num_classes


class StyleLoss(nn.Module):
    def __init__(self):
        super(StyleLoss, self).__init__()
        self.to(_device)

    def forward(self, output, target) -> 'loss':
        target.detach()
        img_size = list(output.size())
        G = gram_matrix(output)
        Gt = gram_matrix(target)
        return F.mse_loss(G, Gt).div((img_size[1] * img_size[2] * img_size[3]))


class PerceptionLoss(_Loss):
    def __init__(self, net, reduction="mean"):
        super(PerceptionLoss, self).__init__()
        self.ref_model = net
        self.ref_model.trainable = False
        self.ref_model.eval()
        self.layer_name_mapping = {'3': "block1_conv2", '8': "block2_conv2", '15': "block3_conv3", '22': "block4_conv3"}
        for name, module in self.ref_model.named_modules():
            if name in list(self.layer_name_mapping.values()):
                module.keep_output = True

        self.reduction = reduction
        self.to(_device)

    def preprocess(self, img):
        return ((img + 1) / 2) * to_tensor([[0.485, 0.456, 0.406]]).unsqueeze(-1).unsqueeze(-1) + to_tensor([[0.229, 0.224, 0.225]]).unsqueeze(-1).unsqueeze(-1)

    def forward(self, output, target) -> 'loss':
        target_features = OrderedDict()
        output_features = OrderedDict()
        _ = self.ref_model(self.preprocess(output))
        for item in self.layer_name_mapping.values():
            output_features[item] = getattr(self.ref_model, item).output

        _ = self.ref_model(self.preprocess(target))
        for item in self.layer_name_mapping.values():
            target_features[item] = getattr(self.ref_model, item).output.detach()

        loss = 0
        num_filters = 0
        for i in range(len(self.layer_name_mapping)):
            b, c, h, w = output_features.value_list[i].shape
            loss += ((output_features.value_list[i] - target_features.value_list[i]) ** 2).sum() / (h * w)
            num_filters += c
        return loss / (output.size(0) * num_filters)


class EdgeLoss(_Loss):
    def __init__(self, reduction="mean"):
        super(EdgeLoss, self).__init__()

        self.reduction = reduction

        self.styleloss = StyleLoss()
        self.to(_device)

    def first_order(self, x, axis=2):
        h, w = x.size(2), x.size(3)
        if axis == 2:
            return (x[:, :, :h - 1, :w - 1] - x[:, :, 1:, :w - 1]).abs()
        elif axis == 3:
            return (x[:, :, :h - 1, :w - 1] - x[:, :, :h - 1, 1:]).abs()
        else:
            return None

    def forward(self, output, target) -> 'loss':
        loss = MSELoss(reduction=self.reduction)(self.first_order(output, 2), self.first_order(target, 2)) + MSELoss(
            reduction=self.reduction)(self.first_order(output, 3), self.first_order(target, 3))
        return loss


class TransformInvariantLoss(nn.Module):
    def __init__(self, loss: _Loss, embedded_func: Layer):
        super(TransformInvariantLoss, self).__init__()
        self.loss = MSELoss(reduction='mean')
        self.coverage = 110
        self.rotation_range = 20
        self.zoom_range = 0.1
        self.shift_range = 0.02
        self.random_flip = 0.3
        self.embedded_func = embedded_func
        self.to(_device)

    def forward(self, output: torch.Tensor, target: torch.Tensor):
        angle = torch.tensor(
            np.random.uniform(-self.rotation_range, self.rotation_range, target.size(0)) * np.pi / 180).float().to(
            output.device)
        scale = torch.tensor(np.random.uniform(1 - self.zoom_range, 1 + self.zoom_range, target.size(0))).float().to(
            output.device)
        height, width = target.size()[-2:]
        center_tensor = torch.tensor([(width - 1) / 2, (height - 1) / 2]).expand(target.shape[0], -1).float().to(
            output.device)
        mat = get_rotation_matrix2d(center_tensor, angle, scale).float().to(output.device)
        rotated_target = warp_affine(target, mat, target.size()[2:]).float().to(output.device)

        embedded_output = self.embedded_func(output)
        embedded_target = self.embedded_func(rotated_target)
        return self.loss(embedded_output, embedded_target)


class GPLoss(nn.Module):
    def __init__(self, discriminator, l=10):
        super(GPLoss, self).__init__()
        self.discriminator = discriminator
        self.l = l

    def forward(self, real_data, fake_data):
        alpha = real_data.new_empty((real_data.size(0), 1, 1, 1)).uniform_(0, 1)
        alpha = alpha.expand_as(real_data)
        interpolates = alpha * real_data + ((1 - alpha) * fake_data)

        interpolates = torch.tensor(interpolates, requires_grad=True)
        disc_interpolates = self.discriminator(interpolates)
        gradients = torch.autograd.grad(outputs=disc_interpolates, output=interpolates,
                                        grad_outputs=real_data.new_ones(disc_interpolates.size()), create_graph=True,
                                        retain_graph=True, only_output=True)[0]
        gradients = gradients.view(gradients.size(0), -1)
        gradients_norm = torch.sqrt(torch.sum(gradients ** 2, dim=1) + 1e-12)
        gradient_penalty = ((gradients_norm - 1) ** 2).mean() * self.l


def get_loss(loss_name):
    if loss_name is None:
        return None
    loss_modules = ['trident.optims.pytorch_losses']
    if loss_name in __all__:
        loss_fn = get_class(loss_name, loss_modules)
    else:
        try:
            loss_fn = get_class(camel2snake(loss_name), loss_modules)
        except Exception:
            loss_fn = get_class(loss_name, loss_modules)
    return loss_fn
