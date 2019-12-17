# no hybridize, just like pytorch
from mxnet.gluon import nn
from mxnet.gluon.nn import Block
from mxnet.gluon.nn import HybridBlock
from mxnet import nd
import random
import mxnet as mx
import mxnet
import numpy as np
from blocks import Shufflenet, Shuffle_Xception
from mxnet import ndarray as F

class ShuffleNetV2_OneShot(Block):
    def __init__(self, input_size=224, n_class=1000, search=True):
        super(ShuffleNetV2_OneShot, self).__init__()

        assert input_size % 32 == 0

        self.stage_repeats = [4, 4, 8, 4]
        self.stage_out_channels = [-1, 16, 64, 160, 320, 640, 1024]
        self.candidate_scales = [0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0]
        input_channel = self.stage_out_channels[1]

        self.first_conv = nn.Sequential(prefix='first_')
        self.first_conv.add(nn.Conv2D(input_channel, in_channels=3, kernel_size=3, strides=2, padding=1, use_bias=False))
        self.first_conv.add(nn.BatchNorm(in_channels=input_channel, momentum=0.1))
        self.first_conv.add(nn.Activation('relu'))
        self.search = search

        self.features = nn.Sequential(prefix='features_')
        archIndex = 0
        for idxstage in range(len(self.stage_repeats)):
            numrepeat = self.stage_repeats[idxstage]
            output_channel = self.stage_out_channels[idxstage + 2]

            for i in range(numrepeat):
                if i == 0:
                    inp, outp, stride = input_channel, output_channel, 2
                else:
                    inp, outp, stride = input_channel, output_channel, 1

                base_mid_channels = outp // 2
                mid_channels = int(base_mid_channels * self.candidate_scales[-1])
                archIndex += 1

                self.features.add(nn.Sequential(prefix=''))

                for blockIndex in range(4):
                    if blockIndex == 0:
                        #print('Shuffle3x3')
                        self.features[-1].add(Shufflenet(inp, outp, mid_channels=mid_channels, ksize=3, stride=stride, search=self.search))
                    elif blockIndex == 1:
                        #print('Shuffle5x5')
                        self.features[-1].add(Shufflenet(inp, outp, mid_channels=mid_channels, ksize=5, stride=stride, search=self.search))
                    elif blockIndex == 2:
                        #print('Shuffle7x7')
                        self.features[-1].add(Shufflenet(inp, outp, mid_channels=mid_channels, ksize=7, stride=stride, search=self.search))
                    elif blockIndex == 3:
                        #print('Xception')
                        self.features[-1].add(Shuffle_Xception(inp, outp, mid_channels=mid_channels, stride=stride, search=self.search))
                    else:
                        raise NotImplementedError

                input_channel = output_channel
        self.archLen = archIndex
        self.conv_last = nn.Sequential(prefix='last_')
        self.conv_last.add(nn.Conv2D(self.stage_out_channels[-1], in_channels=input_channel, kernel_size=1, strides=1, padding=0, use_bias=False))
        self.conv_last.add(nn.BatchNorm(in_channels=self.stage_out_channels[-1],momentum=0.1))
        self.conv_last.add(nn.Activation('relu'))

        self.globalpool = nn.AvgPool2D(7)
        self.output = nn.Sequential(prefix='output_')
        with self.output.name_scope():
            self.output.add(
                nn.Dropout(0.1),
                nn.Dense(units=n_class, in_units=self.stage_out_channels[-1], use_bias=False)
            )


    def _initialize(self, force_reinit=True, ctx=mx.cpu(), dtype='float32'):
        for k, v in self.collect_params().items():
            if 'conv' in k:
                if 'weight' in k:
                    if 'first' in k :#or 'last' in k or 'squeeze' in k or 'excitation' in k or 'output' in k:
                        v.initialize(mx.init.Normal(0.01), force_reinit=force_reinit, ctx=ctx)
                    elif 'transpose' in k:
                        v.initialize(mx.init.Normal(0.01), force_reinit=force_reinit, ctx=ctx)
                        v.set_data(nd.cast(generate_transpose_conv_kernel(v.shape[0]), dtype=dtype))
                        v.grad_req = 'null'
                    else:
                        v.initialize(mx.init.Normal(1.0 / v.shape[1]), force_reinit=force_reinit, ctx=ctx)
                if 'bias' in k:
                    v.initialize(mx.init.Constant(0), force_reinit=force_reinit, ctx=ctx)
            elif 'batchnorm' in k:
                if 'gamma' in k:
                    v.initialize(mx.init.Constant(1), force_reinit=force_reinit, ctx=ctx)
                if 'beta' in k:
                    v.initialize(mx.init.Constant(0.0001), force_reinit=force_reinit, ctx=ctx)
                if 'running' in k or 'moving' in k:
                    v.initialize(mx.init.Constant(0), force_reinit=force_reinit, ctx=ctx)
            elif 'dense' in k:
                v.initialize(mx.init.Normal(0.01), force_reinit=force_reinit, ctx=ctx)
                if 'bias' in k:
                    v.initialize(mx.init.Constant(0), force_reinit=force_reinit, ctx=ctx)


    def forward(self, x, architecture, channel_mask, *args, **kwargs):
        #import pdb
        #pdb.set_trace()
        assert self.archLen == len(architecture)
        x = self.first_conv(x)
        for archs, arch_id in zip(self.features, architecture):
            x = archs[arch_id](x, channel_mask)
        x = self.conv_last(x)
        x = self.globalpool(x)
        x = self.output(x)
        return x

def get_channel_mask(channel_choice, dtype):
    channel_mask = []
    stage_repeats = [4, 8, 4, 4]
    stage_out_channels = [64, 160, 320, 640]
    candidate_scales = [0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0]
    global_max_length = int(stage_out_channels[-1] // 2 * candidate_scales[-1])
    for i in range(len(stage_repeats)):
        for j in range(stage_repeats[i]):
            local_mask = [0] * global_max_length
            channel_choice_index = len(channel_mask)  # channel_choice index is equal to current channel_mask length
            channel_num = int(stage_out_channels[i] // 2 *
                              candidate_scales[channel_choice[channel_choice_index]])
            local_mask[:channel_num] = [1] * channel_num
            channel_mask.append(local_mask)
    channel_mask = nd.array(channel_mask).astype(dtype=dtype, copy=False)
    return channel_mask


if __name__ == "__main__":
    architecture = [0, 0, 3, 1, 1, 1, 0, 0, 2, 0, 2, 1, 1, 0, 2, 0, 2, 1, 3, 2]
    channel_choice = [6, 5, 3, 5, 2, 6, 3, 4, 2, 5, 7, 5, 4, 6, 7, 4, 4, 5, 4, 3]
    channel_mask = get_channel_mask(channel_choice, dtype='float32')
    model = ShuffleNetV2_OneShot(search=True)
    model._initialize(ctx=mxnet.cpu())
    #for blocks in model.features:
        #for block in blocks:
            #for op in block.branch_main:
                #if isinstance(op, nn.BatchNorm):
                    #print(op.running_mean)
    #for k, v in model.collect_params('.*running_mean|.*running_var').items():
        #v.set_data(mx.nd.ones_like(v.data()))
        #print(v.data())
    #for k,v in model._children.items():
        #print(k)
        #print(v)
    test_data = mxnet.nd.random.uniform(-1, 1, shape=(5, 3, 224, 224))
    test_outputs = model(test_data, architecture, channel_mask)
    print(test_outputs.shape)
