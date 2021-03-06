#!/usr/bin/env python

"""
Lasagne implementation of CIFAR-10 examples from "Deep Residual Learning for Image Recognition" (http://arxiv.org/abs/1512.03385)
Check the accompanying files for pretrained models. The 32-layer network (n=5), achieves a validation error of 7.42%, 
while the 56-layer network (n=9) achieves error of 6.75%, which is roughly equivalent to the examples in the paper.
"""

from __future__ import print_function
import sys
import os
import time
import string
import random
import pickle
from parable.data.image_util import *
from parable.logs.logs_util import *
import numpy as np
import theano
import theano.tensor as T
import lasagne

# for the larger networks (n>=9), we need to adjust pythons recursion limit
sys.setrecursionlimit(10000)


def topKAccuracy(preds, targets, k=5):
    topKacc = 0.0
    sorted_preds = np.argsort(preds, axis=1)
    for i in range(sorted_preds.shape[0]):
        top5_preds = sorted_preds[i, -k:]
        if targets[i] in top5_preds:
            topKacc += 1
    return topKacc * 1.0 / sorted_preds.shape[0]
    # return np.mean(np.equal(np.argmax(preds,axis=1), targets)*1.0)


# ##################### Load data from CIFAR-10 dataset #######################
# this code assumes the cifar dataset from 'https://www.cs.toronto.edu/~kriz/cifar-10-python.tar.gz'
# has been extracted in current working directory

def unpickle(file):
    import cPickle
    fo = open(file, 'rb')
    dict = cPickle.load(fo)
    fo.close()
    return dict


def load_data():
    xs = []
    ys = []
    for j in range(5):
        d = unpickle('data/cifar-10-batches-py/data_batch_' + `j + 1`)
        x = d['data']
        y = d['labels']
        xs.append(x)
        ys.append(y)

    d = unpickle('data/cifar-10-batches-py/test_batch')
    xs.append(d['data'])
    ys.append(d['labels'])

    x = np.concatenate(xs) / np.float32(255)
    y = np.concatenate(ys)
    x = np.dstack((x[:, :1024], x[:, 1024:2048], x[:, 2048:]))
    x = x.reshape((x.shape[0], 32, 32, 3)).transpose(0, 3, 1, 2)

    # subtract per-pixel mean
    pixel_mean = np.mean(x[0:50000], axis=0)
    # pickle.dump(pixel_mean, open("cifar10-pixel_mean.pkl","wb"))
    x -= pixel_mean

    # create mirrored images
    X_train = x[0:50000, :, :, :]
    Y_train = y[0:50000]
    X_train_flip = X_train[:, :, :, ::-1]
    Y_train_flip = Y_train
    X_train = np.concatenate((X_train, X_train_flip), axis=0)
    Y_train = np.concatenate((Y_train, Y_train_flip), axis=0)

    X_test = x[50000:, :, :, :]
    Y_test = y[50000:]

    return dict(
        X_train=lasagne.utils.floatX(X_train),
        Y_train=Y_train.astype('int32'),
        X_test=lasagne.utils.floatX(X_test),
        Y_test=Y_test.astype('int32'), )


# ##################### Build the neural network model #######################

# from lasagne.layers import Conv2DLayer as ConvLayer
from lasagne.layers.dnn import Conv2DDNNLayer as ConvLayer
from lasagne.layers import ElemwiseSumLayer, MaxPool2DLayer
from lasagne.layers import InputLayer
from lasagne.layers import DenseLayer
from lasagne.layers import GlobalPoolLayer
from lasagne.layers import PadLayer
from lasagne.layers import ExpressionLayer
from lasagne.layers import NonlinearityLayer
from lasagne.nonlinearities import softmax, rectify
from lasagne.layers import batch_norm


# create a residual learning building block with two stacked 3x3 convlayers as in paper
def residual_block(l, increase_dim=False, projection=False):
    """
    Args:
        increase_dim: controls whether we double out filter number
    Returns:

    """
    input_num_filters = l.output_shape[1]
    if increase_dim:
        first_stride = (2, 2)
        out_num_filters = input_num_filters * 2
    else:
        first_stride = (1, 1)
        out_num_filters = input_num_filters

    stack_1 = batch_norm(
        ConvLayer(l, num_filters=out_num_filters, filter_size=(3, 3), stride=first_stride, nonlinearity=rectify,
                  pad='same', W=lasagne.init.HeNormal(gain='relu')))
    stack_2 = batch_norm(
        ConvLayer(stack_1, num_filters=out_num_filters, filter_size=(3, 3), stride=(1, 1), nonlinearity=None,
                  pad='same', W=lasagne.init.HeNormal(gain='relu')))

    # add shortcut connections
    if increase_dim:
        if projection:
            # projection shortcut, as option B in paper
            projection = batch_norm(
                ConvLayer(l, num_filters=out_num_filters, filter_size=(1, 1), stride=(2, 2), nonlinearity=None,
                          pad='same', b=None))
            block = NonlinearityLayer(ElemwiseSumLayer([stack_2, projection]), nonlinearity=None)
        else:
            # identity shortcut, as option A in paper
            identity = ExpressionLayer(l, lambda X: X[:, :, ::2, ::2], lambda s: (s[0], s[1], s[2] // 2, s[3] // 2))
            padding = PadLayer(identity, [out_num_filters // 4, 0, 0], batch_ndim=1)
            block = NonlinearityLayer(ElemwiseSumLayer([stack_2, padding]), nonlinearity=None)
    else:
        block = NonlinearityLayer(ElemwiseSumLayer([stack_2, l]), nonlinearity=None)

    return block


def resfuse_super_block(l, excessive=True):
    """
    This puts 2 resfuse_block together,
    and connect top to bottom (excessive connection)

    if not excessive, we just return stack2

    We also demand no dimension change
    """
    stack1 = resfuse_block(l)
    stack2 = resfuse_block(stack1)

    block = None
    if excessive:
        block = NonlinearityLayer(ElemwiseSumLayer([stack2, l]), nonlinearity=None)
    else:
        block = stack2

    return block


# create a resfuse learning block with 2 stacked residual block layer
def resfuse_block(l):
    """
    Every resfuse block is made of 2 resblock
    and top connect to bottom
    We simply won't allow dimension increase in a simple
    resfuse_block, dimension increase should be performed
    by a single resnet block layer!
    Args:
        increase_dim: only affect the first resnet block
        excessive: whether we try to connect more, or less
    """
    stack1 = residual_block(l)
    stack2 = residual_block(stack1)

    block = NonlinearityLayer(ElemwiseSumLayer([stack2, l]), nonlinearity=None)

    return block


def build_resfuse_net(input_var=None, n=5, execessive=False):
    # Building the network
    l_in = InputLayer(shape=(None, 3, 64, 64), input_var=input_var)

    # first layer, output is 16 x 64 x 64
    l = batch_norm(ConvLayer(l_in, num_filters=16, filter_size=(3, 3), stride=(1, 1), nonlinearity=rectify, pad='same',
                             W=lasagne.init.HeNormal(gain='relu')))

    # first stack of residual blocks, output is 16 x 64 x 64
    l = resfuse_block(l)
    # 2 resfuse blocks
    l = resfuse_super_block(l, excessive=execessive)

    # second stack of residual blocks, output is 32 x 32 x 32
    l = residual_block(l, increase_dim=True)
    l = resfuse_super_block(l, excessive=execessive)  # 4 res-blocks

    # third stack of residual blocks, output is 64 x 16 x 16
    l = residual_block(l, increase_dim=True)
    l = resfuse_super_block(l, excessive=execessive)  # 4 res-blocks

    # average pooling
    l = GlobalPoolLayer(l)

    # fully connected layer
    network = DenseLayer(
        l, num_units=100,
        W=lasagne.init.HeNormal(),
        nonlinearity=softmax)

    return network


def build_cnn(input_var=None, n=5):
    # Building the network
    # This is a ResNet-34 architecture, same as

    l_in = InputLayer(shape=(None, 3, 64, 64), input_var=input_var)

    # first layer, output is 16 x 64 x 64
    l = batch_norm(ConvLayer(l_in, num_filters=64, filter_size=(3, 3), stride=(1, 1), nonlinearity=rectify, pad='same',
                             W=lasagne.init.HeNormal(gain='relu')))

    # CIFAR-10 doesn't aggressively pool, and ImageNet 128x128 aggressively pools
    l = MaxPool2DLayer(l, 2) # 32 x 32

    # first stack of residual blocks, output is 64 x 32 x 32
    for _ in range(3):
        l = residual_block(l)

    # second stack of residual blocks, output is 128 x 16 x 16
    l = residual_block(l, increase_dim=True)
    for _ in range(3):
        l = residual_block(l)

    # third stack of residual blocks, output is 256 x 16 x 16
    l = residual_block(l, increase_dim=True)
    for _ in range(5):
        l = residual_block(l)

    # fourth stack of residual blocks, output is 512 x 8 x 8
    l = residual_block(l, increase_dim=True)
    for _ in range(2):
        l = residual_block(l)

    # average pooling
    l = GlobalPoolLayer(l)

    # fully connected layer
    network = DenseLayer(
        l, num_units=100,
        W=lasagne.init.HeNormal(),
        nonlinearity=softmax)

    return network


# ############################# Batch iterator ###############################

def iterate_minibatches(inputs, targets, batchsize, shuffle=False, augment=False):
    assert len(inputs) == len(targets)
    if shuffle:
        indices = np.arange(len(inputs))
        np.random.shuffle(indices)
    for start_idx in range(0, len(inputs) - batchsize + 1, batchsize):
        if shuffle:
            excerpt = indices[start_idx:start_idx + batchsize]
        else:
            excerpt = slice(start_idx, start_idx + batchsize)
        if augment:
            # as in paper : 
            # pad feature arrays with 4 pixels on each side
            # and do random cropping of 64x64
            padded = np.pad(inputs[excerpt], ((0, 0), (0, 0), (4, 4), (4, 4)), mode='constant')
            random_cropped = np.zeros(inputs[excerpt].shape, dtype=np.float32)
            crops = np.random.random_integers(0, high=8, size=(batchsize, 2))
            for r in range(batchsize):
                random_cropped[r, :, :, :] = padded[r, :, crops[r, 0]:(crops[r, 0] + 64),
                                             crops[r, 1]:(crops[r, 1] + 64)]
            inp_exc = random_cropped
        else:
            inp_exc = inputs[excerpt]

        yield inp_exc, targets[excerpt]


# ############################## Main program ################################

def main(n=6, num_epochs=30, model=None, **kwargs):
    """
    Args:
        **kwargs:
        - path: direct path to CIFAR-10 or TinyImageNet
        - data: "cifar-10" or "tiny-image-net"
        - type: 'resnet' or 'resfuse' or 'resfuse-max'
    """

    # Unpack keyword arguments
    path = kwargs.pop('path', './cifar-10-batches-py')
    data_name = kwargs.pop('data', 'cifar-10')
    model_type = kwargs.pop('type', 'resnet')

    # Check if cifar data exists
    if not os.path.exists(path):
        print(
            "CIFAR-10 dataset can not be found. Please download the dataset from 'https://www.cs.toronto.edu/~kriz/cifar.html'.")
        print("Or download Tiny-imagenet-A :)")
        return

    # Load the dataset
    print("Loading data...")

    data = None
    if data_name == 'cifar-10':
        data = load_data()
    elif data_name == 'tiny-image-net':
        sub_sample = kwargs.pop('subsample', 0.1)
        data = load_tiny_imagenet(path, sub_sample=sub_sample, subtract_mean=True,
                                  dtype=theano.config.floatX)
        data['X_test'] = data['X_val']
        data['Y_test'] = data['y_val']
        data['Y_train'] = data['y_train']

    X_train = data['X_train']
    Y_train = data['Y_train']
    X_test = data['X_test']
    Y_test = data['Y_test']

    # Prepare Theano variables for inputs and targets
    input_var = T.tensor4('inputs')
    target_var = T.ivector('targets')

    # Create neural network model
    print("Building model and compiling functions...")
    if model_type == 'resnet':  # 'resnet' or 'resfuse' or 'resfuse-max'
        network = build_cnn(input_var, n)
        print("ResNet")
    elif model_type == 'resfuse':
        network = build_resfuse_net(input_var, n, execessive=False)
        print("ResFuse Net")
    elif model_type == 'resfuse-max':
        network = build_resfuse_net(input_var, n, execessive=True)
        print("ResFuse Max Net")
    else:
        raise ValueError("model type must be from resnet, resfuse, resfuse-max")

    print("number of parameters in model: %d" % lasagne.layers.count_params(network, trainable=True))

    if model is None:
        # Create a loss expression for training, i.e., a scalar objective we want
        # to minimize (for our multi-class problem, it is the cross-entropy loss):
        prediction = lasagne.layers.get_output(network)
        loss = lasagne.objectives.categorical_crossentropy(prediction, target_var)
        loss = loss.mean()
        # add weight decay
        all_layers = lasagne.layers.get_all_layers(network)
        l2_penalty = lasagne.regularization.regularize_layer_params(all_layers, lasagne.regularization.l2) * 0.0001
        loss = loss + l2_penalty

        # Create update expressions for training
        # Stochastic Gradient Descent (SGD) with momentum
        params = lasagne.layers.get_all_params(network, trainable=True)
        lr = 0.1
        sh_lr = theano.shared(lasagne.utils.floatX(lr))
        updates = lasagne.updates.momentum(
            loss, params, learning_rate=sh_lr, momentum=0.9)

        # Compile a function performing a training step on a mini-batch (by giving
        # the updates dictionary) and returning the corresponding training loss:
        train_fn = theano.function([input_var, target_var], loss, updates=updates)

    # Create a loss expression for validation/testing
    test_prediction = lasagne.layers.get_output(network, deterministic=True)
    test_loss = lasagne.objectives.categorical_crossentropy(test_prediction,
                                                            target_var)
    test_loss = test_loss.mean()
    test_acc = T.mean(T.eq(T.argmax(test_prediction, axis=1), target_var),
                      dtype=theano.config.floatX)

    # Compile a second function computing the validation loss and accuracy:
    val_fn = theano.function([input_var, target_var], [test_loss, test_acc, test_prediction])

    if model is None:
        # launch the training loop
        print("Starting training...")
        training_start_time = time.time()
        best_val_acc = 0.0

        # We iterate over epochs:
        for epoch in range(num_epochs):
            # shuffle training data
            train_indices = np.arange(X_train.shape[0])
            np.random.shuffle(train_indices)
            X_train = X_train[train_indices, :, :, :]
            Y_train = Y_train[train_indices]

            # In each epoch, we do a full pass over the training data:
            train_err = 0
            train_batches = 0
            start_time = time.time()
            for batch in iterate_minibatches(X_train, Y_train, 128, shuffle=True, augment=True):
                inputs, targets = batch
                train_err += train_fn(inputs, targets)
                train_batches += 1

            # And a full pass over the validation data:
            val_err = 0
            val_acc = 0
            val_batches = 0
            top5accuracy = 0.0
            for batch in iterate_minibatches(X_test, Y_test, 500, shuffle=False):
                inputs, targets = batch
                err, acc, test_prediction = val_fn(inputs, targets)
                top5accuracy += topKAccuracy(test_prediction, targets)
                val_err += err
                val_acc += acc
                val_batches += 1

            # Then we print the results for this epoch:
            print("Epoch {} of {} took {:.3f}m".format(
                epoch + 1, num_epochs, (time.time() - start_time) / 60.0))
            print("  training loss:\t\t{:.6f}".format(train_err / train_batches))
            print("  validation loss:\t\t{:.6f}".format(val_err / val_batches))
            print("  validation accuracy:\t\t{:.2f} %".format(
                val_acc / val_batches * 100))
            print(" top 5 validation accuracy:\t\t{:.2f} %".format(
                top5accuracy / val_batches * 100
            ))

            # adjust learning rate as in paper
            # 32k and 48k iterations should be roughly equivalent to 41 and 61 epochs
            if (epoch + 1) == 55 or (epoch + 1) == 85:
                new_lr = sh_lr.get_value() * 0.1
                print("New LR:" + str(new_lr))
                sh_lr.set_value(lasagne.utils.floatX(new_lr))

            # decay learning rate when a plateau is hit
            # when overall validation acc becomes negative or increases smaller than 0.01
            # we decay learning rate by 0.8
            if (val_acc / val_batches) - best_val_acc <= 0.005:
                    new_lr = sh_lr.get_value() * 0.995
                    print("New LR:" + str(new_lr))
                    sh_lr.set_value(lasagne.utils.floatX(new_lr))

            if (val_acc / val_batches) > best_val_acc:
                best_val_acc = val_acc / val_batches

        # print out total training time
        print("Total training time: {:.3f}m".format((time.time() - training_start_time) / 60.0))

        # dump the network weights to a file :
        npz_file_name = ''
        if data_name == 'cifar-10':
            npz_file_name = 'cifar10_deep_residual_model.npz'
        else:
            npz_file_name = 'tiny_imagen_a_epochs_' + str(num_epochs) + '_n_' + str(n) + "_" \
                            + time_string() + "_model.npz"

        np.savez(npz_file_name, *lasagne.layers.get_all_param_values(network))
    else:
        # load network weights from model file
        with np.load(model) as f:
            param_values = [f['arr_%d' % i] for i in range(len(f.files))]
        lasagne.layers.set_all_param_values(network, param_values)

    # Calculate validation error of model:
    test_err = 0
    test_acc = 0
    test_batches = 0
    for batch in iterate_minibatches(X_test, Y_test, 128, shuffle=False):
        inputs, targets = batch
        err, acc, predictions = val_fn(inputs, targets)
        test_err += err
        test_acc += acc
        test_batches += 1
    print("Final results:")
    print("  test loss:\t\t\t{:.6f}".format(test_err / test_batches))
    print("  test accuracy:\t\t{:.2f} %".format(
        test_acc / test_batches * 100))


if __name__ == '__main__':
    if ('--help' in sys.argv) or ('-h' in sys.argv):
        print("Trains a Deep Residual Learning network on cifar-10 using Lasagne.")
        print(
            "Network architecture and training parameters are as in section 4.2 in 'Deep Residual Learning for Image Recognition'.")
        print("Usage: %s [N [MODEL]]" % sys.argv[0])
        print()
        print("N: Number of stacked residual building blocks per feature map (default: 5)")
        print("MODEL: saved model file to load (for validation) (default: None)")
    else:
        kwargs = {}
        epochs = 100

        if len(sys.argv) > 1:
            kwargs['type'] = sys.argv[1]
        if len(sys.argv) > 2:
            kwargs['n'] = int(sys.argv[2])
        if len(sys.argv) > 3:
            epochs = int(sys.argv[3])
        if len(sys.argv) > 4:
            kwargs['model'] = sys.argv[4]

        kwargs['pwd'] = os.path.dirname(os.path.realpath(__file__))

        kwargs['path'] = kwargs['pwd'] + '/data/tiny-imagenet-100-A'
        kwargs['data'] = 'tiny-image-net'

        kwargs['subsample'] = 1

        main(num_epochs=epochs, **kwargs)
