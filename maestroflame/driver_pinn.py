import yt
import numpy as np
import matplotlib.pyplot as plt
import matplotlib

import os
from IPython.display import Video
from glob import glob
import warnings
import sys
import pandas as pd
import optuna
from optuna.trial import TrialState
from datetime import datetime
# https://github.com/optuna/optuna-examples/blob/main/pytorch/pytorch_simple.py
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
import torch.utils.data
from torch.utils.data.dataset import Dataset
from torch.utils.data import DataLoader

yt.funcs.mylog.setLevel(40) # Gets rid of all of the yt info text, only errors.
warnings.filterwarnings("ignore",category=matplotlib.cbook.mplDeprecation) #ignore plt depreciations
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")


DEBUG_MODE = True
DO_PLOTTING = True
SAVE_MODEL = True
DO_HYPER_OPTIMIZATION = False #TODO


# ------------------------ DATA --------------------------------------------
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


#from reactdataset import ReactDataset
from reactdataset2 import ReactDataset2
from plotting import make_movie

data_path = 'data/data3/flame/'
input_prefix = 'react_inputs_*'
output_prefix = 'react_outputs_*'
plotfile_prefix = 'flame_*'
output_dir = 'fix_plot/'
log_file = output_dir + "log.txt"

if os.path.isdir(output_dir) and (len(os.listdir(output_dir)) != 0):
    print(f"Directory {output_dir} exists and is not empty.")
    print("Please change output_dir or remove the directory to prevent overwritting data.")
    sys.exit()


isdir = os.path.isdir(output_dir)
if not isdir:
    os.mkdir(output_dir)

class Logger(object):
    def __init__(self):
        self.terminal = sys.stdout
        self.log = open(log_file, "a")

    def write(self, message):
        self.terminal.write(message)
        self.log.write(message)

    def flush(self):
        pass


sys.stdout = Logger()

now = datetime.now()
dt_string = now.strftime("%m/%d/%Y %H:%M:%S")
print(f"Model starting on : {dt_string}")
print(f"input_prefix {input_prefix}")
print(f"output_prefix {output_prefix}")
print(f"output_dir {output_dir}")
print(f"DEBUG_MODE {DEBUG_MODE}")
print(f"DO_PLOTTING {DO_PLOTTING}")
print(f"DO_HYPER_OPTIMIZATION {DO_HYPER_OPTIMIZATION}")



plotfiles = glob(data_path + plotfile_prefix)
plotfiles = sorted(plotfiles)
plotfiles = plotfiles[:-2] #cut after divuiter and initproj
plotfiles = [plotfiles[-1]] + plotfiles[:-1] #move initdata to front.
#make_movie(plotfiles, movie_name='enuc.mp4', var='enuc')


react_data = ReactDataset2(data_path, input_prefix, output_prefix, plotfile_prefix, DEBUG_MODE=DEBUG_MODE)
nnuc = int(react_data.output_data.shape[1]/2 - 1)

#Normalize density, temperature, and enuc
dens_fac = torch.max(react_data.input_data[:, nnuc+1, :])
temp_fac = torch.max(react_data.input_data[:, nnuc+2, :])
enuc_fac = torch.max(react_data.output_data[:, nnuc, :])
enuc_dot_fac = torch.max(react_data.output_data[:, 2*(nnuc+1) - 1, :])


#RHS temperature at tn+1 (obtained from calling EOS)
#rhs_fac = torch.max(react_data.output_data[:, 14, :])

react_data.input_data[:, nnuc+1, :]  = react_data.input_data[:, nnuc+1, :]/dens_fac
react_data.input_data[:, nnuc+2, :]  = react_data.input_data[:, nnuc+2, :]/temp_fac
react_data.output_data[:, nnuc, :] = react_data.output_data[:, nnuc, :]/enuc_fac
react_data.output_data[:, 2*(nnuc+1) - 1, :] = react_data.output_data[:, 2*(nnuc+1) - 1, :]/enuc_dot_fac

#dpndx[enuc] = enuc_fac/enuc_dot_fac * dpndx[enuc]

#percent cut for testing
percent_test = 10
N = len(react_data)

Num_test  = int(N*percent_test/100)
Num_train = N-Num_test

train_set, test_set = torch.utils.data.random_split(react_data, [Num_train, Num_test])

train_loader = DataLoader(dataset=train_set, batch_size=16, shuffle=True)
test_loader = DataLoader(dataset=test_set, batch_size=16, shuffle=True)


# ------------------------ NEURAL NETWORK -----------------------------------
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
from networks import Net, OC_Net, Deep_Net


if DEBUG_MODE:
    num_epochs = 3
else:
    num_epochs = 100


if DO_HYPER_OPTIMIZATION:
    BATCHSIZE = 16
    CLASSES = react_data.output_data.shape[1]
    in_features = react_data.input_data.shape[1]
    EPOCHS = 10
    LOG_INTERVAL = 10
    N_TRAIN_EXAMPLES = BATCHSIZE * 30
    N_VALID_EXAMPLES = BATCHSIZE * 10
    #hyperparameter space
    LAYERS = [1,5]
    UNITS = [4, 128] #nodes per layer
    DROPOUT_RATE = [0, 0.5]
    LEARNING_RATE = [1e-7, 1e-1]
    OPTIMIZERS = ["Adam", "RMSprop", "SGD"]
    #optimizer study
    if DEBUG_MODE:
        n_trials = 100
    else:
        n_trials=100
    timeout=600

    from hyperparamter_optimization import do_h_opt
    hyper_results = do_h_opt(train_loader, test_loader, BATCHSIZE, CLASSES, EPOCHS,
                          LOG_INTERVAL, N_TRAIN_EXAMPLES, N_VALID_EXAMPLES,
                          LAYERS, UNITS, DROPOUT_RATE, LEARNING_RATE, OPTIMIZERS,
                          n_trials, timeout, in_features)

    model = OC_Net(react_data.input_data.shape[1], react_data.output_data.shape[1]/2, hyper_results)


    optimizer = hyper_results['optimizer']
    lr = hyper_results['lr']
    if optimizer == 'Adam':
        optimizer = optim.Adam(model.parameters(), lr=lr)
    elif optimizer == 'RMSprop':
        optimizer = optim.RMSprop(model.parameters(), lr=lr)
    elif optimizer == 'SGD':
        optimizer = optim.SGD(model.parameters(), lr=lr)
    else:
        print("unsupported optimizer, please define it.")
        sys.exit()

else:

    #model = Net(react_data.input_data.shape[1], 64, 128, 64, react_data.output_data.shape[1]//2)
    model = Deep_Net(react_data.input_data.shape[1], 32, 32, 32, 32, 32, 32, 32, react_data.output_data.shape[1]//2)
    optimizer = optim.Adam(model.parameters(), lr=1e-6)


# ------------------------ LOSS ---------------------------------------------
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
from losses import component_loss_f, loss_pinn, rms_weighted_error
from losses import log_loss, loss_mass_fraction, component_loss_f_L1, relative_loss
from losses import derivative_loss_piecewise, signed_loss_function


#As we test different loss functions, its important to keep a consistent one when
#plotting or else we have no way of comparing them.
def criterion_plotting(prediction, targets):
    L = nn.MSELoss()
    return L(prediction, targets[:, :nnuc+1])

def criterion(data, pred, dXdt, actual):
    #I still don't understand this one but i think don does
    #loss1 = rms_weighted_error(pred, actual[:, :nnuc+1], actual[:, :nnuc+1])

    # #difference in state variables vs prediction.
    # loss1 = log_loss(pred, actual[:, :nnuc+1])
    # #physics informed loss
    # loss2 = loss_pinn(data, pred, actual, enuc_fac, enuc_dot_fac, log_option=True)
    # #sum of mass fractions must be 1
    # loss3 = loss_mass_fraction(pred)
    # #relative loss function.
    # loss4 = relative_loss(pred, actual[:, :nnuc+1])


    #difference in state variables vs prediction.
    loss1 = log_loss(pred, actual[:, :nnuc+1])
    #scaled rates (pinn part) This only scales the magnitude of rates
    #loss2 = derivative_loss_piecewise(dXdt, actual[:, nnuc+1:])
    L = nn.L1Loss()
    loss2 = L(dXdt, actual[:, nnuc+1:])

    #here we learn the sign of rates to make up for not doing that in loss2
    loss3 = signed_loss_function(dXdt, actual[:, nnuc+1:])
    #relative loss function. Helps disginguish between same errors of different
    #scales since we're scaling the loss2 so heavily
    loss4 = relative_loss(pred, actual[:, :nnuc+1])
    #sum of mass fractions must be 1
    loss5 = loss_mass_fraction(pred)
    #sum of rates must be 0
    #loss6 = loss_rates_mass_frac(dXdt, actual[:, nnuc+1:])

    # loss_arr = [loss1.item(), loss2.item(), loss3.item(), loss4.item(), loss5.item()]
    # return  loss1 + loss2 + loss3  + loss4 + loss5, loss_arr
    loss_arr = [loss1.item(), loss2.item(), loss3.item(), loss5.item()]
    return  loss1 +  loss2 + loss3  + loss5, loss_arr


#plot storage
cost_per_epoc = [] #stores total loss at each epoch
component_losses_test = [] #stores component wise loss at each epoch (test data)
component_losses_train = [] #stores component wise loss at each epoch (train data)
d_component_losses_test = [] #stores derivative component wise loss at each epoch (test data)
d_component_losses_train = [] #stores derivative component wise loss at each epoch (train data)
cost_per_epoc_test = []
different_loss_metrics = [] #list of arrays of the various loss metrics defined in criterion
# Train Network

for epoch in range(num_epochs):
    losses = []
    plotting_losses = []
    diff_losses = []

    for batch_idx, (data, targets) in enumerate(train_loader):
        # Get data to cuda if possible
        data = data.to(device=device)
        data.requires_grad=True

        targets = targets.to(device=device)

        # forward
        prediction = model(data)

        # calculate derivatives
        dXdt = torch.zeros_like(prediction)
        for n in range(nnuc+1):
            if data.grad is not None:
                #sets componnents to zero if not already zero
                data.grad.data.zero_()

            prediction[:, n].backward(torch.ones_like(prediction[:,n]), retain_graph=True)
            dXdt[:, n] = data.grad.clone()[:, 0]
            data.grad.data.zero_()

        dXdt.requires_grad = True
        loss, array_loss = criterion(data, prediction, dXdt, targets)

        losses.append(loss.item())
        diff_losses.append(array_loss)

        # backward
        optimizer.zero_grad()
        loss.backward()

        # gradient descent or adam step
        optimizer.step()


        #PLOTTING TERMS
        loss_plot = criterion_plotting(prediction, targets)
        plotting_losses.append(loss_plot.item())

        loss_c = component_loss_f(prediction, targets[:, :nnuc+1])
        loss_c = np.array(loss_c.tolist())

        #L1 loss bc big errors at first squaring big numbers results in nans
        dloss_c = component_loss_f_L1(dXdt, targets[:, nnuc+1:])
        dloss_c = np.array(dloss_c.tolist())

        if batch_idx == 0:
            component_loss = loss_c
            d_component_loss = dloss_c

        else:
            component_loss = component_loss + loss_c
            d_component_loss = d_component_loss + dloss_c


    print(f"Cost at epoch {epoch} is {sum(losses) / len(losses)}")
    #Cost per epoc
    cost_per_epoc.append(sum(plotting_losses) / len(plotting_losses))

    #cost_per_epoc.append(sum(losses) / len(losses))
    component_losses_train.append(component_loss/batch_idx)
    d_component_losses_train.append(d_component_loss/batch_idx)

    diff_losses = np.array(diff_losses)
    different_loss_metrics.append(diff_losses.sum(axis=0)/len(losses))


    if DO_PLOTTING:

        for batch_idx, (data, targets) in enumerate(test_loader):
            # forward
            data.requires_grad=True

            prediction = model(data)
            loss = criterion_plotting(prediction, targets)
            losses.append(loss.item())

            # calculate derivatives
            dXdt = torch.zeros_like(prediction)
            for n in range(nnuc+1):
                if data.grad is not None:
                    #sets componnents to zero if not already zero
                    data.grad.data.zero_()

                prediction[:, n].backward(torch.ones_like(prediction[:,n]), retain_graph=True)
                dXdt[:, n] = data.grad.clone()[:, 0]
                data.grad.data.zero_()


            # -- Component and Deritivave component loss
            loss_c = component_loss_f(prediction, targets[:, :nnuc+1])
            loss_c = np.array(loss_c.tolist())

            #L1 loss bc big errors at first squaring big numbers results in nans
            dloss_c = component_loss_f_L1(dXdt, targets[:, nnuc+1:])
            dloss_c = np.array(dloss_c.tolist())

            if batch_idx == 0:
                component_loss = loss_c
                d_component_loss = dloss_c
            else:
                component_loss = component_loss + loss_c
                d_component_loss = d_component_loss + dloss_c


        cost_per_epoc_test.append(sum(losses) / len(losses))
        component_losses_test.append(component_loss/batch_idx)
        d_component_losses_test.append(d_component_loss/batch_idx)


component_losses_train = np.array(component_losses_train)
component_losses_test = np.array(component_losses_test)
d_component_losses_train = np.array(d_component_losses_train)
d_component_losses_test = np.array(d_component_losses_test)
different_loss_metrics = np.array(different_loss_metrics)


if SAVE_MODEL:
    print("Saving...")
    file_name = output_dir + 'my_model_pinn.pt'
    if os.path.exists(file_name):
        print("Overwritting file:", file_name)
        os.rename(file_name, file_name+'.backup')

    torch.save(model.state_dict(), file_name)
    np.savetxt(output_dir + "/cost_per_epoch.txt", cost_per_epoc)
    np.savetxt(output_dir + "/component_losses_test.txt", component_losses_test)
    np.savetxt(output_dir + "/component_losses_train.txt", component_losses_train)
    np.savetxt(output_dir + "/d_component_losses_test.txt", d_component_losses_test)
    np.savetxt(output_dir + "/d_component_losses_train.txt", d_component_losses_train)


if DO_PLOTTING:
    print("Plotting...")
    from plotting import plotting_pinn
    fields = [field[1] for field in yt.load(react_data.output_files[0])._field_list]

    plot_class = plotting_pinn(model, fields, test_loader, cost_per_epoc, component_losses_test,
                component_losses_train, d_component_losses_test, d_component_losses_train,
                cost_per_epoc_test, different_loss_metrics,  output_dir)

    plot_class.do_all_plots()


print("Success! :) \n")
sys.stdout.log.close()
