"""
implementation of LassoNet where the hierarchical penalty is applied to the convolutional filters.  

some snippets from: https://medium.com/dataseries/visualizing-the-feature-maps-and-filters-by-convolutional-neural-networks-e1462340518e
"""

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from typing import Callable

from module import hier_prox

# from: https://discuss.pytorch.org/t/utility-function-for-calculating-the-shape-of-a-conv-output/11173/6
def conv_output_shape(h_w, kernel_size=1, stride=1, pad=0, dilation=1):
    """
    Utility function for computing output of convolutions
    takes a tuple of (h,w) and returns a tuple of (h,w)
    """
    
    if type(h_w) is not tuple:
        h_w = (h_w, h_w)
    
    if type(kernel_size) is not tuple:
        kernel_size = (kernel_size, kernel_size)
    
    if type(stride) is not tuple:
        stride = (stride, stride)
    
    if type(pad) is not tuple:
        pad = (pad, pad)
    
    h = (h_w[0] + (2 * pad[0]) - (dilation * (kernel_size[0] - 1)) - 1)// stride[0] + 1
    w = (h_w[1] + (2 * pad[1]) - (dilation * (kernel_size[1] - 1)) - 1)// stride[1] + 1
    
    return h, w

class ConvLassoNet(nn.Module):
    def __init__(self, lambda_ = 1., M = 1., D_in = (28,28), D_out = 10, out_channels1 = 16, out_channels2 = 32, stride=1, padding=2, dilation=1):
        """
        LassoNet applied after a first layer of convolutions. See https://jmlr.org/papers/volume22/20-848/20-848.pdf for details.

        Parameters
        ----------
        lambda_ : float, optional
            Penalty parameter for the skip layer. The default is 1.
            By setting to ``None``, the LassoNet penalty is deactivated.
        M : float, optional
            Penalty parameter for the hierarchical constraint. The default is 1.
        D_in : int, optional
            input dimension of the model. The default is 784.
        D_out : int, optional
            output dimension of the model. The default is 10.

        Returns
        -------
        None.

        """
        
        super(ConvLassoNet, self).__init__()
        
        # LassoNet penalty
        self.lambda_ = lambda_
        self.M = M
        
        assert (self.lambda_ is None) or (self.lambda_ > 0), "lambda_ must be None or positive"
        assert self.M > 0, "M needs to be positive (possibly np.inf)"
        
        # hyperparameters (works as long as input size can be divided by 4)
        self.kernel_size1 = 5
        self.kernel_size2 = 5
        self.out_channels1 = out_channels1
        self.out_channels2 = out_channels2
        
        self.D_in = D_in
        self.D_out = D_out
        self.conv1_output_dim(stride, padding, dilation)
        
        # first conv layer and skip layer
        # input pixels nxn, filter size fxf, padding p: output size (n + 2p — f + 1)
        self.conv1 = nn.Conv2d(1, self.out_channels1, kernel_size=self.kernel_size1, stride=stride, padding=padding, dilation=dilation)
        
        if self.lambda_ is not None:
            self.skip = nn.Linear(self.out_channels1*self.h_out*self.w_out, self.D_out)
        
        # remaining nonlinear part (after conv1)
        self.relu = nn.ReLU()
        self.maxpool = nn.MaxPool2d(kernel_size=2, stride=2)
        self.conv2 = nn.Conv2d(self.out_channels1, self.out_channels2, kernel_size=self.kernel_size2, stride=1, padding=2)
        self.drop_out = nn.Dropout()
        # downsampling twice by factor 2 --> 7x7 output, 64 channels
        self.fc1 = nn.Linear(int((self.D_in[0]*self.D_in[1])/(4*4)) *self.out_channels2, 200)
        self.fc2 = nn.Linear(200, self.D_out)
        
        return
    
    def conv1_output_dim(self, stride, padding, dilation):
        """ computes output dimension of conv1; needed for dimensionality of skip layer
        """
        self.h_out, self.w_out = conv_output_shape(self.D_in, self.kernel_size1, stride, padding, dilation)
        
        return
        
    def forward(self, x):
        out = self.conv1(x)
        
        if self.lambda_ is not None:
            z1 = self.skip(out.reshape(-1, self.out_channels1*self.h_out*self.w_out))
        else: 
            z1 = 0.
            
        # rest of non-linear part
        out = self.relu(out)
        out = self.maxpool(out)
        out = self.conv2(out)
        out = self.relu(out)
        out = self.maxpool(out)
        out = out.reshape(out.size(0), -1)
        out = self.drop_out(out)
        out = self.fc1(out)
        z2 = self.fc2(out)
        return z1+z2
    
    def prox(self, lr):
        # loop through output channels / filters
        for j in range(self.out_channels1):
            theta_j = self.skip.weight.data[:,self.h_out*self.w_out*j:self.h_out*self.w_out*(j+1)].reshape(-1)
            filter_j = self.conv2.weight[:,j,:,:].reshape(-1)
            
            theta_j, filter_j = hier_prox(theta_j, filter_j, lambda_=self.lambda_*lr, lambda_bar=0, M = self.M)
            
            self.skip.weight.data[:,self.h_out*self.w_out*j:self.h_out*self.w_out*(j+1)] = theta_j.reshape(self.D_out, -1)
            self.conv2.weight.data[:,j,:,:] = filter_j.reshape(self.out_channels2, self.kernel_size2, self.kernel_size2) 
        return
    

    def do_training(self, loss: torch.nn.Module, dl: DataLoader, opt: torch.optim.Optimizer=None, n_epochs: int=10, lr_schedule: torch.optim.lr_scheduler.StepLR=None,\
                    valid_ds: Dataset=None, preprocess: Callable=None, verbose: bool=True):
        """

        Parameters
        ----------
        loss : ``torch.nn.Module`` loss function
            Loss function for the model.
        dl : ``torch.utils.data.DataLoader``
            DataLoader with the training data.
        opt : from ``torch.optim``, optional
            Pytorch optimizer. The default is SGD with Nesterov momentum and learning rate 0.001.
        n_epochs : int, optional
            Number of epochs for training. The default is 10.
        lr_schedule : from ``torch.optim.lr_scheduler``, optional
            Learning rate schedule. Step is taken after each epoch. The default is None.
        valid_ds : ``torch.utils.data.Dataset``, optional
            Dataset for validation loss. The default is None.
        preprocess : function, optional
            A function for preprocessing the inputs for the model. The default is None.
        verbose : boolean, optional
            Verbosity. The default is True.

        Returns
        -------
        info : dict
            Training and validation loss and accuracy history. Each entry is the loss/accuracy averaged over one epoch.

        """
        if opt is None:
            opt = torch.optim.SGD(self.parameters(), lr = 0.001, momentum = 0.9, nesterov = True)
        
        if verbose:
            print(opt)    
        
        info = {'train_loss':[],'valid_loss':[],'train_acc':[],'valid_acc':[]}
        
        
        for j in np.arange(n_epochs):
            
            ################### SETUP FOR EPOCH ##################
            all_loss = list(); all_acc = list()
            all_vl_loss = list(); all_vl_acc = list()
            
            ################### START OF EPOCH ###################
            self.train()
            for inputs, targets in dl:
                if preprocess is not None:
                    inputs = preprocess(inputs)
                
                # forward pass
                y_pred = self.forward(inputs)
                # compute loss
                loss_val = loss(y_pred, targets)           
                # zero gradients
                opt.zero_grad()    
                # backward pass
                loss_val.backward()    
                # iteration
                opt.step()
                # step size
                alpha = opt.state_dict()['param_groups'][0]['lr']
                # prox step
                if self.lambda_ is not None:
                    self.prox(alpha)
                
                ## COMPUTE ACCURACY AND STORE 
                #print(loss_val.item())
                scores, predictions = torch.max(y_pred.data, 1)
                all_loss.append(loss_val.item())
                all_acc.append((predictions == targets).float().mean())
                
                
            ################### END OF EPOCH ###################
            if lr_schedule is not None:
                lr_schedule.step()    
            
            ### VALIDATION
            # if valid_ds is not None:
            #     self.eval()
            #     output = self.forward(valid_ds.X)
            #     v_loss = loss(output, v_targets)
            #     v_scores, v_predictions = torch.max(output.data, 1)
            #     v_correct = (v_predictions == v_targets).float().mean()
                
            #     all_vl_loss.append(v_loss.item())
            #     all_vl_acc.append(v_correct.item())
        
            ### STORE
            
            info['train_loss'].append(np.mean(all_loss))
            info['train_acc'].append(np.mean(all_acc))
            # if valid_ds is not None:
            #     info['valid_loss'].append()
            #     info['valid_acc'].append()
            
            if verbose:
                print(f"Epoch {j+1}/{n_epochs}: \t  train loss: {np.mean(all_loss)}, \t train accuracy: {np.mean(all_acc)}.")
                print(opt)    
            
        return info
        



